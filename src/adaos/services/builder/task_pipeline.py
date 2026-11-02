"""Native Prototype task admission before candidate generation.

One durable interpretation job belongs to the same trusted user turn as generation
and repairs. Observation/restart reuses that job; it never starts paid work twice.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from collections.abc import Mapping

from adaos.services.builder_context import canonical, digest
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from .generation_plan import compile_generation_plan, context_receipt
from .task_interpretation import admit_interpretation, TaskInterpretationRejected
from .task_spec import compile_task_spec, verify_identity, selected_target_ref


class TaskInterpretationProviderFailed(TaskInterpretationRejected):
    """A terminal transport result is not a rejected interpretation proposal."""

    def __init__(self, result):
        from adaos.services.ai.provider_rejection import safe_rejection
        rejection = safe_rejection((result.get('tool_telemetry') or {}).get('provider_rejection')
            or (result.get('telemetry') or {}).get('provider_rejection'))
        self.provider_rejection = rejection
        self.job_id = str(result.get('job_id') or '')
        status = result.get('status')
        self.provider_status = status if isinstance(status,str) and status in {'failed','cancelled','interrupted','queued','running'} else 'unknown'
        super().__init__([{'code':'task.interpretation_provider_failed', 'gate':'G6',
            'owner':'context_delivery', 'detail':'The interpretation provider did not return an admissible proposal. '
            'The exact job result is retained; candidate generation and automatic resubmission are forbidden.'}])


def _observed_provider_calls(result):
    count = (result.get('telemetry') or {}).get('provider_calls')
    return count if type(count) is int and count >= 0 else None


def enabled(metadata=None):
    value = (metadata or {}).get("builder_task_pipeline", os.getenv("ADAOS_BUILDER_TASK_PIPELINE_ENABLED", "0"))
    if value not in {True, False, "0", "1"}:
        raise ValueError("Builder task pipeline switch must be 0 or 1")
    return value in {True, "1"}


def _save(path, value):
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    atomic_write_json(path, value)


def admit_task_source(instruction, *, metadata=None):
    metadata = metadata or {}
    intent = metadata.get("prototype_intent")
    if not isinstance(intent, Mapping):
        intent = capture_intent(instruction, locale=metadata.get("locale"),
            project_ref=selected_target_ref(metadata),
            change_ref=metadata.get("builder_change_ref"),
            source={"kind": metadata.get("prototype_request_source", "chat"),
                    "ref": metadata.get("source_message_id") or metadata.get("message_id")})
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    if intent["statement"] != instruction.strip():
        raise TaskInterpretationRejected([{'code': 'task.source_mismatch', 'gate': 'G0',
            'owner': 'source_admission', 'detail': 'Active source and Prototype task instruction disagree'}])
    selected = selected_target_ref(metadata)
    if selected and intent['scope']['project_ref'] != selected:
        raise TaskInterpretationRejected([{'code': 'task.target_scope_conflict', 'gate': 'G0',
            'owner': 'source_admission', 'detail': 'The supplied Intent belongs to a different selected target'}])
    brief = metadata.get("prototype_brief") or compile_prototype_brief(intent)
    task = compile_task_spec(intent, brief)
    return {"intent": copy.deepcopy(intent), "brief": copy.deepcopy(brief), "task_spec": task}


def _retained_task(journal_dir, brief):
    """An S1 caller needs an existing Core admission, not a self-sealed claim."""
    verify_identity(brief, id_key='brief_id', prefix='brief:')
    paths = [*Path(journal_dir).glob('builder-interpretation-*.json'), *Path(journal_dir).glob('generation-plan-*.json'),
             *Path(journal_dir).glob('interpretation-validation-*.json'), *Path(journal_dir).glob('deterministic-source-*.json')]
    for path in paths:
        row = json.loads(path.read_text(encoding='utf-8'))
        saved_brief = row.get('brief') or {}
        saved_task = row.get('admitted_task') or row.get('task_spec')
        admitted = (row.get('admission') or {}).get('status') == 'admitted' or row.get('schema') == 'adaos.builder.generation_admission.v1'
        if admitted and saved_brief.get('digest') == brief['digest'] and saved_task:
            verify_identity(saved_brief, id_key='brief_id', prefix='brief:')
            verify_identity(saved_task, id_key='task_spec_id', prefix='task:')
            if (saved_task['brief_digest'] == brief['digest'] and saved_task['interpretation_status'] == 'admitted'
                    and saved_task['source']['digest'] == brief['intent_digest']):
                return copy.deepcopy(saved_task)
    raise TaskInterpretationRejected([{'code': 'task.seed_admission_missing', 'gate': 'G3',
        'owner': 'task_interpretation', 'detail': 'Single-stage generation requires an exact retained Core-admitted Brief and TaskSpec'}])


def _exact_source_replay(intent, previous_brief, journal_dir, current_seed):
    contract = (previous_brief or {}).get('task_contract') or {}
    old_source = contract.get('source') or {}
    if (any(old_source.get(key) != intent.get(key) for key in ('statement', 'scope', 'authority', 'locale', 'explicit_refs'))
            or current_seed.get('interface_context') != previous_brief.get('interface_context')):
        return None
    retained = _retained_task(journal_dir, previous_brief)
    from .task_spec import seal
    brief = copy.deepcopy(previous_brief)
    brief.update(intent_ref=intent['intent_id'], intent_digest=intent['digest'])
    brief['task_contract'].update(source=copy.deepcopy(intent), previous_digest=digest(contract),
        derivation={'kind': 'exact_source_replay', 'prior_brief_ref': previous_brief['brief_id'],
            'prior_task_ref': retained['task_spec_id'], 'prior_source_digest': old_source['digest'],
            'meaning_source_digest': (contract.get('derivation') or {}).get('meaning_source_digest', old_source['digest'])})
    brief = seal(brief, id_key='brief_id', prefix='brief:')
    task = compile_task_spec(intent, brief)
    roles = {row['clause_id']: row['roles'] for row in contract['clause_roles']}
    task = seal({**task, 'clauses': [{**row, 'classification': roles[row['id']]} for row in task['clauses']]})
    receipt = {'schema': 'adaos.builder.task_replay_receipt.v1', 'status': 'admitted',
        'source_digest': intent['digest'], 'prior_source_digest': old_source['digest'],
        'prior_brief_ref': previous_brief['brief_id'], 'prior_task_ref': retained['task_spec_id'],
        'admitted_task_digest': task['digest'], 'brief_digest': brief['digest'], 'provider_calls': 0,
        'admission_basis': 'byte_equal_statement_scope_and_authority_with_retained_core_admission'}
    return task, brief, receipt


def _recover_admitted_interpretation(intent, previous_brief, journal_dir, current_seed):
    """Resume admitted meaning after pre-generation failure, not paid inference.

    The accepted history basis must still be exact. Several different retained
    admissions are ambiguous, not permission to choose a convenient proposal.
    """
    history = (previous_brief or {}).get('task_contract')
    if previous_brief and not history:
        return None
    basis = digest(history) if history else None
    matches = []
    for path in Path(journal_dir).glob('builder-interpretation-*.json'):
        record = json.loads(path.read_text(encoding='utf-8'))
        saved = record.get('brief') or {}
        if ((record.get('admission') or {}).get('status') != 'admitted'
                or (saved.get('task_contract') or {}).get('previous_digest') != basis):
            continue
        source = (saved.get('task_contract') or {}).get('source') or {}
        if source.get('digest')==intent['digest']:
            # The identical ingress request retains its original request/plan
            # identity through _interpret's durable same-job observation path.
            continue
        if any(source.get(key) != intent.get(key) for key in ('statement','scope','authority','locale','explicit_refs')):
            continue
        # Revalidate the retained raw proposal under today's Core admission.
        # A historical "admitted" bit alone cannot bypass corrected rules.
        from .interpretation_delivery import decode_proposal
        replayed_task, replayed_brief = admit_interpretation(record['task_spec'],
            decode_proposal(record), source, previous_brief=previous_brief)
        if (replayed_brief['digest'] != saved['digest']
                or replayed_task['digest'] != record['admitted_task']['digest']):
            raise TaskInterpretationRejected([{'code':'task.recovery_admission_changed','gate':'G3',
                'owner':'task_interpretation','detail':'Retained interpretation no longer has the same current Core admission'}])
        replay = _exact_source_replay(intent, saved, journal_dir, current_seed)
        if replay:
            matches.append((record, replay))
    if not matches:
        return None
    if len(matches) != 1:
        raise TaskInterpretationRejected([{'code':'task.recovery_admission_ambiguous','gate':'G3',
            'owner':'task_interpretation','detail':'Several exact admitted interpretations require explicit recovery selection'}])
    record, replay = matches[0]
    task, brief, receipt = replay
    receipt.update(recovered_interpretation_job_id=record['job_id'], history_basis_digest=basis,
        recovery='admitted_meaning_before_generation_with_unchanged_accepted_history')
    return task, brief, receipt


def _project_has_admitted_meaning(request,metadata,journal_dir):
    """A first-generation failure must not silently restore the legacy route."""
    intent=(metadata or {}).get('prototype_intent') or (request.get('task_source') or {}).get('intent') or {}
    project=selected_target_ref(metadata) or (intent.get('scope') or {}).get('project_ref')
    if not project:
        return False
    for path in [*Path(journal_dir).glob('builder-interpretation-*.json'),
                 *Path(journal_dir).glob('interpretation-validation-*.json')]:
        try:
            record=json.loads(path.read_text(encoding='utf-8'))
            task=record.get('admitted_task') or {}
            brief=record.get('brief') or {}
            if ((record.get('admission') or {}).get('status')!='admitted'
                    or (task.get('scope') or {}).get('project_ref')!=project):
                continue
            verify_identity(task,id_key='task_spec_id',prefix='task:')
            verify_identity(brief,id_key='brief_id',prefix='brief:')
            if task['interpretation_status']!='admitted' or task['brief_digest']!=brief['digest']:
                raise ValueError('Retained task/Brief identity conflict')
            return True
        except (ValueError,KeyError) as exc:
            raise TaskInterpretationRejected([{'code':'task.adoption_receipt_invalid','gate':'G0',
                'owner':'source_admission','detail':'Retained Core task adoption cannot be verified; legacy fallback is forbidden'}]) from exc
    return False


def _interpret(provider, task, intent, *, model, reasoning, prior_brief, journal_dir, timeout, run_observer=None, output_mode=None):
    from .interpretation_delivery import request_packet,decode_proposal
    packet=request_packet(task,previous_brief=prior_brief,output_mode=output_mode)
    messages,contract = packet['messages'],packet['wire_schema']
    options = {"model": model, "reasoning": reasoning, "text": {"format": {
        "type": "json_schema", "name": packet['wire_name'], "strict": True, "schema": contract}},
        "tools": [], "tool_choice": "none", "stream": False, "profile_scope": "development",
        "provider_max_attempts": 1}
    context = context_receipt(messages, generation_options=options)
    from .interpretation_revalidation import validator_digest
    admission_policy = validator_digest()
    request_id = "builder-interpretation-" + digest({"task": task["digest"], "options": options,
        "prior": (prior_brief or {}).get("digest"), "context": context,
        "admission_policy": admission_policy})[7:39]
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    from .interpretation_ingress import admit_ingress
    admit_ingress(journal_dir, task=task, intent=intent, request_id=request_id,
        context=context, prior_brief=prior_brief)
    path = journal_dir / (request_id + ".json")
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("context") != context:
            raise ValueError("Retained interpretation input identity changed")
        if record.get('status')=='execution_run_blocked':
            from .interpretation_runs import InterpretationRunRejected
            raise InterpretationRunRejected()
    else:
        record = {"schema": "adaos.builder.interpretation_attempt.v1", "request_id": request_id,
                  "task_spec": task, "messages": messages, "options": options, "context": context,
                  "status": "reserved", "job_id": None, "provider_calls": None,
                  "admission_policy_digest": admission_policy}
        record['interpretation_output_mode']=packet['mode']
        # Exclusive reservation distinguishes a recoverable job from a crash at
        # the submission boundary. An uncertain reservation cannot be retried.
        with path.open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
        if run_observer is not None:
            try:
                record['execution_run_binding'] = run_observer.bind(task,request_id)
            except TaskInterpretationRejected as error:
                record.update(status='execution_run_blocked',run_findings=error.findings)
                _save(path,record)
                raise
            _save(path,record)
        try:
            response = provider.submit(messages, request_id=request_id, timeout=min(timeout, 60), **options)
            record.update(status=response["status"], job_id=response["job_id"], provider_calls=_observed_provider_calls(response),
                          base_url=response.get("_client", {}).get("base_url"))
            _save(path, record)
        except BaseException:
            record["status"] = "submission_outcome_unknown"
            _save(path, record)
            if run_observer is not None and record.get('execution_run_binding'):
                run_observer.observe(record['execution_run_binding'],record,'submission_unknown')
            raise
        if run_observer is not None and record.get('execution_run_binding'):
            run_observer.observe(record['execution_run_binding'],record,'submitted')
    if record.get("result") is None:
        if not record.get("job_id"):
            raise ValueError("Interpretation submission outcome is unknown; recover its exact request ID before continuing")
        result = provider.wait(record["job_id"], timeout_s=timeout, base_url=record.get("base_url"))
        record.update(result=result, status=result.get("status"), provider_calls=_observed_provider_calls(result))
        _save(path, record)
    else:
        result = record["result"]
    if result.get("status") not in {"succeeded", "completed"}:
        if run_observer is not None and record.get('execution_run_binding'):
            run_observer.observe(record['execution_run_binding'],record,'cancelled' if result.get('status')=='cancelled' else 'failed')
        raise TaskInterpretationProviderFailed(result)
    try:
        proposal = decode_proposal(record)
        task, brief = admit_interpretation(task, proposal, intent, previous_brief=prior_brief)
    except TaskInterpretationRejected as exc:
        record.update(admission={'status': 'rejected', 'findings': exc.findings}, status='interpretation_rejected')
        _save(path, record)
        if run_observer is not None and record.get('execution_run_binding'):
            run_observer.observe(record['execution_run_binding'],record,'failed')
        raise
    receipt = {"schema": "adaos.builder.interpretation_receipt.v1", "request_id": request_id,
               "job_id": record["job_id"], "source_digest": intent["digest"], "proposal_digest": digest(proposal),
               "admitted_task_digest": task["digest"], "brief_digest": brief["digest"],
               "logical_stage": "interpretation", "provider_calls": record["provider_calls"],
               "usage": copy.deepcopy(result.get("telemetry", {}).get("usage", result.get("usage", {}))),
               "context": context, "status": "admitted"}
    from .source_literals import literal_coverage
    from .task_interpretation import _attribute_literal
    _, receipt['source_literal_coverage'] = literal_coverage(record['task_spec'],proposal,literal_matches=_attribute_literal)
    record.update(admission=receipt, admitted_task=task, brief=brief)
    _save(path, record)
    if run_observer is not None and record.get('execution_run_binding'):
        run_observer.observe(record['execution_run_binding'],record,'admitted')
    return task, brief, receipt


def prepare_generation_request(request, *, metadata, model, reasoning, provider,
                               journal_dir, previous_brief=None, timeout=540, run_observer=None,
                               generation_run_observer=None):
    active=enabled(metadata)
    profile = metadata.get('builder_semantic_input_profile')
    interpretation_mode=metadata.get('builder_task_interpretation_mode','model_assisted')
    if interpretation_mode not in {'model_assisted','deterministic_facts'}:
        raise ValueError('Unknown task interpretation mode')
    if profile is not None:
        from .semantic_input import input_contract_pin
        input_contract_pin(profile)  # Refuse unknown profiles before any interpretation call.
    protocol = metadata.get('builder_generation_run_protocol')
    if protocol is not None and protocol != 'v1':
        raise TaskInterpretationRejected([{'code':'task.generation_protocol_unsupported','gate':'G5',
            'owner':'context_compilation','detail':'The requested generation Run protocol is not supported'}])
    if request.get('output_mode')!='semantic_v2':
        if interpretation_mode=='deterministic_facts':
            raise ValueError('Deterministic fresh source requires semantic_v2 task preparation')
        if profile is not None:
            raise ValueError('Semantic input profile requires semantic_v2 task preparation')
        if protocol:
            raise TaskInterpretationRejected([{'code':'task.generation_protocol_requires_semantic_v2','gate':'G5',
                'owner':'context_compilation','detail':'The generation Run protocol requires semantic_v2; legacy unbound fallback is forbidden'}])
        return request
    if protocol and (not callable(getattr(run_observer,'bind_generation',None))
            or not callable(getattr(generation_run_observer,'reserve',None))):
        raise TaskInterpretationRejected([{'code':'task.generation_protocol_unavailable','gate':'G5',
            'owner':'context_compilation','detail':'The requested generation Run protocol needs its Core origin and execution observers'}])
    if not active and not (previous_brief or {}).get('task_contract') and not _project_has_admitted_meaning(request,metadata,journal_dir):
        if interpretation_mode=='deterministic_facts':
            raise ValueError('Deterministic fresh source requires explicit TaskSpec pipeline admission')
        if profile is not None:
            raise ValueError('Semantic input profile requires explicit TaskSpec pipeline admission')
        if protocol:
            raise TaskInterpretationRejected([{'code':'task.generation_protocol_requires_task','gate':'G5',
                'owner':'context_compilation','detail':'Generation Run preparation requires explicit TaskSpec pipeline admission'}])
        return request
    if profile is not None:
        from .input_composition import composition_contract, scoped_instruction
        system_prompt = scoped_instruction(request.get('system_prompt'))
        # Freeze the caller's declared instructions before any interpretation.
        # The final task body is checked by its existing source/Brief pins.
        composition = composition_contract([
            {'role':'system','content':system_prompt},
            {'role':'user','content':request.get('stable_user_prompt')},
            {'role':'user','content':request.get('capability_user_prompt')},
            {'role':'user','content':request.get('user_prompt')}])
    else:
        composition = None
    result = copy.deepcopy(request)
    if profile is not None:
        result['system_prompt'] = system_prompt
    dynamic = result["dynamic_request"]
    instruction = dynamic["instruction"]
    supplied = result.get('task_source')
    source = admit_task_source(instruction, metadata={**metadata, **({'prototype_intent': supplied['intent'],
        'prototype_brief': supplied['brief']} if supplied else {})})
    intent, seed, task = source["intent"], source["brief"], source["task_spec"]
    from .source_literals import source_preflight_findings
    source_findings = source_preflight_findings(task)
    if source_findings:
        _save(Path(journal_dir) / ('source-preflight-' + task['digest'][7:39] + '.json'), {
            'schema':'adaos.builder.source_preflight_refusal.v1','status':'rejected',
            'task_spec':task,'findings':source_findings,
            'interpretation_submitted':False,'candidate_generation_submitted':False})
        raise TaskInterpretationRejected(source_findings)
    deterministic_receipt=None
    if interpretation_mode=='deterministic_facts' and not previous_brief:
        from .deterministic_task import admit_facts
        task,seed,deterministic_receipt=admit_facts(task,intent)
        target=Path(journal_dir)/('deterministic-source-'+task['digest'][7:39]+'.json')
        record={'schema':'adaos.builder.deterministic_source_admission.v1','task_spec':task,'brief':seed,
                'admission':{'status':'admitted'},'receipt':deterministic_receipt}
        if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=record:
            raise TaskInterpretationRejected([{'code':'task.deterministic_admission_conflict','gate':'G3',
                'owner':'task_interpretation','detail':'An immutable deterministic source admission cannot be replaced'}])
        if not target.exists():_save(target,record)
    if previous_brief:
        verify_identity(previous_brief, id_key='brief_id', prefix='brief:')
        prior_contract = previous_brief.get('task_contract')
        if not prior_contract:
            raise TaskInterpretationRejected([{'code': 'task.legacy_history_unadmitted', 'gate': 'G3',
                'owner': 'task_interpretation',
                'detail': 'Interpret the retained legacy requirements before replacing their execution authority'}])
        if prior_contract['source']['scope']['project_ref'] != intent['scope']['project_ref']:
            raise TaskInterpretationRejected([{'code': 'task.history_scope_conflict', 'gate': 'G0',
                'owner': 'source_admission', 'detail': 'Prior task history belongs to another Project'}])
    transport = "personal" if metadata.get("builder_ai_resource_ref") else "root"
    selected_strategy = metadata.get('builder_context_strategy') or os.getenv('ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY', 'full')
    retrieval = metadata.get("builder_context_retrieval") or ('core_prefetch' if selected_strategy == 'core-prefetch' else (
        "model_mcp" if os.getenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "0") == "1" else "inline"))
    strategy = metadata.get("builder_context_strategy") or ('core-prefetch' if retrieval == 'core_prefetch'
        else os.getenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "full"))
    if retrieval == 'model_mcp':
        from .prototype_mcp import enabled as mcp_enabled, issue_protocol
        if not mcp_enabled():
            raise TaskInterpretationRejected([{'code': 'task.retrieval_unavailable', 'gate': 'G4',
                'owner': 'capability_admission', 'detail': 'Read-only MCP is disabled; choose complete inline or Core-prefetch context'}])
        # Read-only discovery/pin admission precedes the interpretation model,
        # not just the later generation submit. Credentials stay in Core.
        issue_protocol()
    provider_limit = int(os.getenv('ADAOS_AI_BUILDER_PROVIDER_CALL_LIMIT', '4')) if transport == 'personal' else 4
    if 'builder_provider_call_limit' in metadata:
        provider_limit=metadata['builder_provider_call_limit']
        if type(provider_limit) is not int:
            raise ValueError('The project provider call limit must be an explicit integer')
    if transport=='personal' and not 1<=provider_limit<=4:
        raise ValueError('Personal Builder supports a whole-turn provider call limit from 1 to 4')
    reuse=metadata.get('builder_interpretation_reuse',True)
    if type(reuse) is not bool:raise ValueError('Interpretation reuse must be an explicit boolean')
    if not reuse and seed.get('task_contract'):
        raise TaskInterpretationRejected([{'code':'task.fresh_interpretation_seed_conflict','gate':'G3',
            'owner':'source_admission','detail':'Fresh interpretation requires unadmitted current source, not a supplied admitted seed'}])
    replay = _exact_source_replay(intent, previous_brief, journal_dir, seed) if reuse and previous_brief else None
    if reuse and not replay:
        replay = _recover_admitted_interpretation(intent, previous_brief, journal_dir, seed)
    if reuse and not replay:
        from .interpretation_revalidation import recover_rejected_first_interpretation
        replay = recover_rejected_first_interpretation(intent,previous_brief=previous_brief,
            journal_dir=journal_dir,current_seed=seed,current_semantic=dynamic.get('current_semantic'),
            exact_replay=_exact_source_replay)
    if replay:
        task, seed, replay_receipt = replay
    elif seed.get('task_contract'):
        task = _retained_task(journal_dir, seed)
    if seed.get('task_contract'):
        from .source_literals import contract_literal_coverage
        from .task_interpretation import _attribute_literal
        source_findings, _ = contract_literal_coverage(seed['task_contract'],literal_matches=_attribute_literal)
        from .source_relations import contract_relation_findings
        source_findings += contract_relation_findings(seed['task_contract'])[0]
        from .source_obligations import contract_obligation_findings
        source_findings += contract_obligation_findings(seed['task_contract'],require_derived=True)
        if source_findings:
            raise TaskInterpretationRejected(source_findings)
    from .interpretation_boundary import reference_context
    reference_targets = reference_context(dynamic.get('current_semantic'))
    if reference_targets is not None:
        from .task_spec import seal
        task = seal({**task, 'reference_context': reference_targets})
    plan = compile_generation_plan(task, model=model, reasoning=reasoning, transport=transport,
        retrieval=retrieval, context_strategy=strategy,
        provider_call_limit=provider_limit,
        candidate_repair_limit=metadata.get('builder_candidate_repair_limit'),
        baseline_digest=digest(dynamic.get("current_semantic")) if dynamic.get("current_semantic") else None)
    if seed.get("task_contract"):
        brief, receipt = seed, deterministic_receipt or (replay_receipt if replay else None)
        route = "single_stage"
    elif interpretation_mode=='deterministic_facts':
        from .deterministic_task import admit_facts
        admit_facts(task,intent,previous_brief=previous_brief)
        raise AssertionError('A changed retained source cannot use fresh deterministic admission')
    else:
        task, brief, receipt = _interpret(provider, task, intent, model=model, reasoning=reasoning,
            prior_brief=previous_brief, journal_dir=journal_dir, timeout=timeout,run_observer=run_observer,
            output_mode=metadata.get('builder_interpretation_output_mode'))
        route = "two_stage"
    from adaos.services.ui_capabilities import selected_ui_capabilities
    selection = selected_ui_capabilities(instruction, intent=intent, prototype_brief=brief, limit=12)
    result["prototype_brief"] = brief
    from .prototype_context import compile_prototype_model_context, task_output_locales
    baseline = dynamic.get('current_semantic') or {}
    baseline_document = baseline.get('document') if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1' else baseline
    from .source_field_bindings import binding_context
    source_bindings = binding_context(baseline,brief) if profile is not None and baseline_document else []
    output_locales = list(task_output_locales(brief,baseline_document))
    result['output_locales'] = output_locales
    dynamic['output_locales'] = output_locales
    result['base_request']['output_locales'] = output_locales
    dynamic["prototype_brief"] = compile_prototype_model_context(brief, compact=True)
    result["user_prompt"] = canonical({"builder_request": dynamic})
    result["base_request"].update(prototype_brief=dynamic["prototype_brief"], selected_ui_capabilities=selection)
    # The capability projection must be rebuilt from the admitted interpretation,
    # without repeating a common policy paragraph for each selected item.
    result["capability_user_prompt"] = canonical({"semantic_capability_context": {
        "schema": "adaos.builder.semantic_capability_context.v1", "brief_ref": brief["brief_id"],
        "items": [{key: copy.deepcopy(row[key]) for key in ("id", "kind", "title", "summary", "postconditions")
                   if key in row} for row in selection.get("items", [])],
        "policy": "Selected recommendations are not a semantic allowlist. Core owns renderers; all required bindings/assets must be admitted independently."}})
    result["task_spec"] = task
    result["generation_preflight_plan"] = plan
    from .task_preservation import fixture_write_scope
    try:
        write_scope = fixture_write_scope(dynamic.get('current_semantic'), brief)
    except TaskInterpretationRejected as error:
        identity = digest({'source': task['source']['digest'], 'baseline': dynamic.get('current_semantic')})[7:39]
        _save(Path(journal_dir) / ('task-preflight-' + identity + '.json'), {
            'schema':'adaos.builder.task_preflight_refusal.v1', 'status':'rejected',
            'task_spec':task, 'brief':brief, 'baseline':dynamic.get('current_semantic'),
            'findings':error.findings, 'candidate_generation_submitted':False})
        raise
    from .semantic_prototype import semantic_prototype_provider_contract
    from .task_preservation import fixture_provider_contract
    output_schema = semantic_prototype_provider_contract(version='v2',locales=tuple(output_locales),brief=brief,
        requirement_mode='owner_local' if profile is not None else 'explicit')
    output_schema = fixture_provider_contract(output_schema,baseline,write_scope)
    from .task_interpretation import _provider_schema_capacity
    _provider_schema_capacity(output_schema,stage='generation')
    result['provider_output_schema'] = output_schema
    if write_scope:
        dynamic['write_scope'] = write_scope
    dynamic['task_spec_ref'] = task['task_spec_id']
    if profile is not None:
        composition=composition_contract([
            {'role':'system','content':result['system_prompt']},
            {'role':'user','content':result['stable_user_prompt']},
            {'role':'user','content':result['capability_user_prompt']},
            {'role':'user','content':canonical({'builder_request':dynamic})}])
    result["generation_plan"] = compile_generation_plan(task, model=model, reasoning=reasoning, transport=transport,
        retrieval=retrieval, context_strategy=strategy, route=route, baseline_digest=plan["baseline_digest"],
        provider_call_limit=provider_limit,
        candidate_repair_limit=metadata.get('builder_candidate_repair_limit'),
        capability_digest=digest(selection), write_scope=write_scope, output_locales=output_locales,
        output_schema_digest=digest(output_schema), model_context_digest=digest(dynamic['prototype_brief']),
        semantic_input_profile=metadata.get('builder_semantic_input_profile'),
        capability_context_digest=digest(json.loads(result['capability_user_prompt'])['semantic_capability_context']),
        input_composition=composition,
        source_field_bindings=source_bindings,
        execution_origin_required=callable(getattr(run_observer,'bind_generation',None)),
        execution_run_required=protocol=='v1')
    result["interpretation_receipt"] = receipt
    if write_scope:
        dynamic['write_scope'] = write_scope
    dynamic["generation_plan_ref"] = result["generation_plan"]["generation_plan_id"]
    dynamic["task_spec_ref"] = task["task_spec_id"]
    result["user_prompt"] = canonical({"builder_request": dynamic})
    from .generation_origin import retain_generation_admission
    retained = retain_generation_admission(Path(journal_dir) / (result["generation_plan"]["generation_plan_id"].replace(":", "-") + ".json"),
          {"schema": "adaos.builder.generation_admission.v1", "task_spec": task,
           'brief': brief,
           'provider_output_schema':output_schema,
           'baseline':dynamic.get('current_semantic'),
           "generation_plan": result["generation_plan"], "interpretation_receipt": receipt,
           "capability_selection_digest": digest(selection)},observer=run_observer)
    if protocol:
        result['execution_run_binding'] = generation_run_observer.reserve(task,result['generation_plan'],
            retained['execution_origin'],journal_dir=journal_dir)['binding']
    return result
