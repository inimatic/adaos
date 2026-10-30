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
from .task_interpretation import admit_interpretation, interpretation_contract, interpretation_messages, TaskInterpretationRejected
from .task_spec import compile_task_spec, verify_identity


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
        target = metadata.get("builder_selected_target") or {}
        intent = capture_intent(instruction, locale=metadata.get("locale"),
            project_ref="project:" + str(target["object_id"]) if target.get("object_type") == "project" else None,
            change_ref=metadata.get("builder_change_ref"),
            source={"kind": metadata.get("prototype_request_source", "chat"),
                    "ref": metadata.get("source_message_id") or metadata.get("message_id")})
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    if intent["statement"] != instruction.strip():
        raise ValueError("Active source and Prototype task instruction disagree")
    brief = metadata.get("prototype_brief") or compile_prototype_brief(intent)
    task = compile_task_spec(intent, brief)
    return {"intent": copy.deepcopy(intent), "brief": copy.deepcopy(brief), "task_spec": task}


def _interpret(provider, task, intent, *, model, reasoning, prior_brief, journal_dir, timeout):
    messages = interpretation_messages(task, previous_brief=prior_brief)
    contract = interpretation_contract(provider=True, task=task)
    options = {"model": model, "reasoning": reasoning, "text": {"format": {
        "type": "json_schema", "name": "adaos_builder_task_interpretation", "strict": True, "schema": contract}},
        "tools": [], "tool_choice": "none", "stream": False, "profile_scope": "development",
        "provider_max_attempts": 1}
    context = context_receipt(messages, generation_options=options)
    request_id = "builder-interpretation-" + digest({"task": task["digest"], "options": options,
        "prior": (prior_brief or {}).get("digest"), "context": context})[7:39]
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / (request_id + ".json")
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("context") != context:
            raise ValueError("Retained interpretation input identity changed")
    else:
        record = {"schema": "adaos.builder.interpretation_attempt.v1", "request_id": request_id,
                  "task_spec": task, "messages": messages, "options": options, "context": context,
                  "status": "reserved", "job_id": None, "provider_calls": 0}
        # Exclusive reservation distinguishes a recoverable job from a crash at
        # the submission boundary. An uncertain reservation cannot be retried.
        with path.open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
        try:
            response = provider.submit(messages, request_id=request_id, timeout=min(timeout, 60), **options)
            record.update(status=response["status"], job_id=response["job_id"], provider_calls=1,
                          base_url=response.get("_client", {}).get("base_url"))
            _save(path, record)
        except BaseException:
            record["status"] = "submission_outcome_unknown"
            _save(path, record)
            raise
    if record.get("result") is None:
        if not record.get("job_id"):
            raise ValueError("Interpretation submission outcome is unknown; recover its exact request ID before continuing")
        result = provider.wait(record["job_id"], timeout_s=timeout, base_url=record.get("base_url"))
        record.update(result=result, status=result.get("status"), provider_calls=result.get("telemetry", {}).get("provider_calls", 1))
        _save(path, record)
    else:
        result = record["result"]
    if result.get("status") not in {"succeeded", "completed"}:
        raise ValueError("Interpretation job did not complete: " + str(result.get("error") or result.get("status")))
    proposal = json.loads(result.get("output_text") or "")
    try:
        task, brief = admit_interpretation(task, proposal, intent, previous_brief=prior_brief)
    except TaskInterpretationRejected as exc:
        record.update(admission={'status': 'rejected', 'findings': exc.findings}, status='interpretation_rejected')
        _save(path, record)
        raise
    receipt = {"schema": "adaos.builder.interpretation_receipt.v1", "request_id": request_id,
               "job_id": record["job_id"], "source_digest": intent["digest"], "proposal_digest": digest(proposal),
               "admitted_task_digest": task["digest"], "brief_digest": brief["digest"],
               "logical_stage": "interpretation", "provider_calls": record["provider_calls"],
               "usage": copy.deepcopy(result.get("telemetry", {}).get("usage", result.get("usage", {}))),
               "context": context, "status": "admitted"}
    record.update(admission=receipt, admitted_task=task, brief=brief)
    _save(path, record)
    return task, brief, receipt


def prepare_generation_request(request, *, metadata, model, reasoning, provider,
                               journal_dir, previous_brief=None, timeout=540):
    if (not enabled(metadata) and not (previous_brief or {}).get("task_contract")) or request.get("output_mode") != "semantic_v2":
        return request
    result = copy.deepcopy(request)
    dynamic = result["dynamic_request"]
    instruction = dynamic["instruction"]
    source = result.get("task_source") or admit_task_source(instruction, metadata=metadata)
    intent, seed, task = source["intent"], source["brief"], source["task_spec"]
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
    plan = compile_generation_plan(task, model=model, reasoning=reasoning, transport=transport,
        retrieval=retrieval, context_strategy=strategy,
        provider_call_limit=provider_limit,
        baseline_digest=digest(dynamic.get("current_semantic")) if dynamic.get("current_semantic") else None)
    if seed.get("task_contract"):
        # Explicit source-bound contracts may be replayed as a single model stage.
        task = copy.deepcopy(task)
        task["interpretation_status"] = "admitted"
        from .task_spec import seal
        task = seal(task)
        brief, receipt = seed, None
        route = "single_stage"
    else:
        task, brief, receipt = _interpret(provider, task, intent, model=model, reasoning=reasoning,
            prior_brief=previous_brief, journal_dir=journal_dir, timeout=timeout)
        route = "two_stage"
    from adaos.services.ui_capabilities import selected_ui_capabilities
    selection = selected_ui_capabilities(instruction, intent=intent, prototype_brief=brief, limit=12)
    result["prototype_brief"] = brief
    from .prototype_context import compile_prototype_model_context
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
    result["generation_plan"] = compile_generation_plan(task, model=model, reasoning=reasoning, transport=transport,
        retrieval=retrieval, context_strategy=strategy, route=route, baseline_digest=plan["baseline_digest"],
        provider_call_limit=provider_limit,
        capability_digest=digest(selection))
    result["interpretation_receipt"] = receipt
    dynamic["generation_plan_ref"] = result["generation_plan"]["generation_plan_id"]
    dynamic["task_spec_ref"] = task["task_spec_id"]
    result["user_prompt"] = canonical({"builder_request": dynamic})
    _save(Path(journal_dir) / (result["generation_plan"]["generation_plan_id"].replace(":", "-") + ".json"),
          {"schema": "adaos.builder.generation_admission.v1", "task_spec": task,
           "generation_plan": result["generation_plan"], "interpretation_receipt": receipt,
           "capability_selection_digest": digest(selection)})
    return result
