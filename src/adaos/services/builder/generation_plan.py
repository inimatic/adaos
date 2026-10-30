"""Core-owned generation stages, context identities and wire admission."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping

from adaos.services.builder_context import canonical, digest
from .task_spec import active_requirements, seal, verify_identity


class GenerationInputRejected(ValueError):
    def __init__(self, code, detail):
        self.findings = [{"code": code, "detail": detail, "gate": "G6", "owner": "context_compilation"}]
        super().__init__(code + ": " + detail)


def compile_generation_plan(task, *, model, transport, retrieval="inline", context_strategy=None,
                            reasoning=None, route=None, baseline_digest=None, capability_digest=None,
                            provider_call_limit=4, tool_call_limit=4, write_scope=None, output_locales=None,
                            output_schema_digest=None, model_context_digest=None, execution_origin_required=False):
    active_requirements(task)
    if transport not in {"personal", "root"} or retrieval not in {"inline", "core_prefetch", "model_mcp"}:
        raise ValueError("Unknown generation transport or retrieval mode")
    strategies = {'inline': {'full', 'adaptive'}, 'core_prefetch': {'core-prefetch'},
        'model_mcp': {'full', 'hybrid', 'hybrid-focused', 'core-indexed', 'task-aware', 'task-aware-compact'}}
    context_strategy = context_strategy or ('core-prefetch' if retrieval == 'core_prefetch' else 'full')
    if context_strategy not in strategies[retrieval]:
        raise ValueError('Context strategy does not match its admitted retrieval mode')
    route = route or ("single_stage" if task["interpretation_status"] == "admitted" else "two_stage")
    if route not in {"single_stage", "two_stage", "deterministic", "capability_gap"}:
        raise ValueError("Unknown generation route")
    if route == "single_stage" and task["interpretation_status"] != "admitted":
        raise ValueError("Single-stage generation needs an admitted TaskSpec")
    if type(provider_call_limit) is not int or not 1 <= provider_call_limit <= 16:
        raise ValueError("Provider call budget must be an integer from 1 to 16")
    if type(execution_origin_required) is not bool:
        raise ValueError("Execution origin policy must be boolean")
    stages = [] if route in {"deterministic", "capability_gap"} else ["generation"]
    if route == "two_stage":
        stages.insert(0, "interpretation")
    if len(stages) > provider_call_limit:
        raise ValueError("Provider call budget cannot cover the admitted generation stages")
    from .prototype_mcp import library_snapshot
    return seal({"schema": "adaos.builder.generation_plan.v1", "task_spec_ref": task["task_spec_id"],
        **({'execution_origin_required':True} if execution_origin_required else {}),
        "task_spec_digest": task["digest"], "brief_ref": task["brief_ref"], "brief_digest": task["brief_digest"],
        "source_digest": task['source']['digest'], "instruction_digest": digest(task['source']['statement']),
        'guidance_library_digest': library_snapshot()['library_digest'],
        "baseline_digest": baseline_digest, "capability_digest": capability_digest,
        'write_scope': copy.deepcopy(write_scope),
        'output_locales': copy.deepcopy(output_locales),
        'output_schema_digest': output_schema_digest,
        'model_context_digest':model_context_digest,
        "route": route, "interpretation": "model_assisted" if route == "two_stage" else "admitted",
        "retrieval": retrieval, "transport": transport, "context_strategy": context_strategy,
        "acceptance_digest": digest(task["acceptance"]), "provider_call_limit": provider_call_limit,
        "tool_call_limit": tool_call_limit, "stages": [{"name": name, "model": model,
            "reasoning": copy.deepcopy(reasoning), "retrieval": "inline" if name == "interpretation" else retrieval,
            "output_contract": "adaos.builder.task_interpretation.v1" if name == "interpretation" else
                               "adaos.builder.semantic_prototype_candidate.v2"} for name in stages],
        "repair_policy": "diagnose_before_scoped_candidate_repair; shared_turn_provider_budget"},
        id_key="generation_plan_id", prefix="generation-plan:")


def context_receipt(messages, *, generation_options=None):
    """Record actual units and authority without copying their text into metadata."""
    units = []
    for index, row in enumerate(messages):
        content = row.get("content", "")
        try:
            body = json.loads(content)
        except (ValueError, TypeError):
            body = None
        kind = next(iter(body)) if isinstance(body, dict) and len(body) == 1 else "text"
        units.append({"index": index, "role": row.get("role"), "kind": kind,
                      "digest": digest(row), "utf8_bytes": len(str(content).encode("utf-8")),
                      "purpose": {"stable_builder_context": "platform_contract", "builder_request": "task",
                                  "semantic_repair": "candidate_repair", "platform_contracts": "platform_contract",
                                  "generation_plan": "execution_plan"}.get(kind, "instruction_or_evidence")})
    options = generation_options or {}
    return {"schema": "adaos.builder.context_plan_receipt.v1", "messages_digest": digest(messages),
            "units": units, "output_contract_digest": digest(options.get("text") or {}),
            "model": options.get("model"), "reasoning": copy.deepcopy(options.get("reasoning")),
            "tool_count": len(options.get("tools") or [])}


def admit_generation_input(messages, *, model, text=None, reasoning=None, tools=None,
                           tool_choice=None, transport, plan=None):
    """Check the actual post-composition envelope on both provider transports.

    The receipt intentionally contains digests and tool counts, never descriptor
    credentials. Platform-fact admission remains the shared component service.
    """
    bodies = []
    required_retrieval_refs = []
    for row in messages:
        try:
            body = json.loads(row.get("content", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(body, dict):
            bodies.append(body)
    requests = [body["builder_request"] for body in bodies if isinstance(body.get("builder_request"), dict)]
    stable = [body["stable_builder_context"] for body in bodies if isinstance(body.get("stable_builder_context"), dict)]
    semantic = any(isinstance(row.get("semantic_invariants"), dict) and
                   row["semantic_invariants"].get("contract") == "adaos.builder.semantic_prototype_candidate.v2"
                   for row in stable)
    if not semantic and plan is None:
        return None
    for row in stable:
        retrieval = row.get("prototype_retrieval")
        if retrieval and retrieval.get("dimensions") and (not tools or tool_choice == "none"):
            raise GenerationInputRejected("context.required_retrieval_unavailable",
                "Omitted required guidance has no callable retrieval; recompile complete inline context before submission")
    if requests and (text or {}).get("format", {}).get("type") == "json_schema":
        contract = text["format"].get("schema") or {}
        supplied = contract.get("$defs", {}).get("requirementBinding", {}).get("properties", {}).get("requirement_ref", {}).get("enum")
        expected = {item["id"] for item in requests[-1].get("prototype_brief", {}).get("required_references", [])}
        if expected and (supplied is None or set(supplied) != expected):
            raise GenerationInputRejected("context.requirement_projection_conflict",
                "Model requirement inventory and strict candidate schema must use the same exact refs")
    if plan is not None:
        verify_identity(plan, id_key="generation_plan_id", prefix="generation-plan:")
        if plan["transport"] != transport:
            raise GenerationInputRejected("context.transport_conflict", "Selected generation transport changed")
        if not any(row["model"] == model and row["reasoning"] == reasoning for row in plan["stages"]):
            raise GenerationInputRejected("context.profile_conflict", "Actual model/reasoning differs from the admitted stage")
        if requests:
            if plan.get('context_stage')=='repair':
                raise GenerationInputRejected('context.repair_carrier_conflict','A repair plan cannot authorize a fresh generation carrier')
            if len(requests) != 1 or len(stable) != 1:
                raise GenerationInputRejected('context.carrier_ambiguous', 'Generation needs exactly one task and one platform carrier')
            request = requests[0]
            brief = request.get('prototype_brief') or {}
            if plan.get('model_context_digest') and digest(brief)!=plan['model_context_digest']:
                raise GenerationInputRejected('context.model_context_conflict','The canonical task model-context projection changed after admission')
            if (request.get('task_spec_ref') != plan['task_spec_ref']
                    or request.get('generation_plan_ref') != plan['generation_plan_id']
                    or brief.get('brief_ref') != plan['brief_ref'] or brief.get('brief_digest') != plan['brief_digest']):
                raise GenerationInputRejected('context.task_identity_conflict', 'Wire task, Brief and generation-plan identities must agree')
            if digest(request.get('instruction')) != plan['instruction_digest']:
                raise GenerationInputRejected('context.source_conflict', 'The source instruction changed after task admission')
            acceptance = brief.get('task_acceptance') or {}
            if digest({key: acceptance.get(key) for key in ('facts', 'transitions', 'constraints')}) != plan['acceptance_digest']:
                raise GenerationInputRejected('context.acceptance_conflict', 'Wire input lost or changed the independent source assertions')
            baseline = digest(request['current_semantic']) if request.get('current_semantic') else None
            if baseline != plan['baseline_digest']:
                raise GenerationInputRejected('context.baseline_conflict', 'The semantic baseline changed after planning')
            if request.get('write_scope') != plan.get('write_scope'):
                raise GenerationInputRejected('context.write_scope_conflict', 'The Core-admitted write scope changed or disappeared')
            output = (text or {}).get('format') or {}
            discriminator = (output.get('schema') or {}).get('properties', {}).get('schema', {})
            identity = discriminator.get('const') or next(iter(discriminator.get('enum', [])), None)
            if output.get('type') != 'json_schema' or output.get('strict') is not True or identity != 'adaos.builder.semantic_prototype_candidate.v2':
                raise GenerationInputRejected('context.output_contract_conflict', 'Generation must use the admitted strict semantic-v2 output contract')
            if plan.get('output_schema_digest') and digest(output.get('schema')) != plan['output_schema_digest']:
                raise GenerationInputRejected('context.output_schema_conflict', 'The actual output schema differs from the Core-admitted grammar')
            if plan.get('output_locales') is not None:
                localized = output.get('schema', {}).get('$defs', {}).get('localizedText') or {}
                expected_locales = set(plan['output_locales'])
                if (request.get('output_locales') != plan['output_locales']
                        or set(localized.get('properties', {})) != expected_locales
                        or set(localized.get('required', [])) != expected_locales):
                    raise GenerationInputRejected('context.output_locale_conflict', 'Task, baseline locale closure and provider schema disagree')
            from .prototype_mcp import library_snapshot
            snapshot = library_snapshot()
            if plan['guidance_library_digest'] != snapshot['library_digest']:
                raise GenerationInputRejected('context.platform_pin_changed', 'The planned guidance authority changed before submission')
            facts = stable[0].get('semantic_invariants') or {}
            retrieval = stable[0].get('prototype_retrieval') or {}
            missing = []
            for fragment in snapshot['fragments']:
                key = fragment['id']
                actual = (facts.get('ux_recommendations') or {}).get(key[3:]) if key.startswith('ux.') else facts.get(key)
                if actual is not None and digest(actual) != fragment['content_digest']:
                    raise GenerationInputRejected('context.platform_fact_conflict', 'Inline guidance differs from its pinned authority: ' + key)
                if actual is None and not key.startswith('ux.') and key not in {'media', 'revision_scope'}:
                    missing.append(key)
            if missing and (plan['retrieval'] != 'model_mcp' or retrieval.get('library_digest') != snapshot['library_digest']
                    or not retrieval.get('available') or not tools or tool_choice == 'none'):
                raise GenerationInputRejected('context.platform_dependency_missing', 'Mandatory guidance is neither inline nor callable: ' + ', '.join(missing))
            if missing:
                required_retrieval_refs = sorted(missing)
                indexed = {identity for dimension in retrieval.get('dimensions', [])
                    for identity in ([dimension['id']] if dimension.get('id') else dimension.get('ids', []))}
                from .prototype_mcp import OPENAI_TOOLS, SERVER_LABEL
                callable_guide = any(tool.get('type') == 'mcp' and tool.get('server_label') == SERVER_LABEL
                    and set(tool.get('allowed_tools', [])) == set(OPENAI_TOOLS) for tool in tools)
                if set(missing) - indexed or not callable_guide:
                    raise GenerationInputRejected('context.retrieval_closure_incomplete',
                        'Every omitted mandatory fragment needs an exact indexed locator and the admitted guidance toolset')
        else:
            repairs = [body['semantic_repair'] for body in bodies if isinstance(body.get('semantic_repair'),dict)]
            if len(repairs)!=1 or plan.get('context_stage')!='repair':
                raise GenerationInputRejected('context.task_carrier_missing', 'A planned repair needs one Core-admitted repair carrier')
            repair=repairs[0]
            brief=repair.get('prototype_brief') or {}
            if plan.get('model_context_digest') and digest(brief)!=plan['model_context_digest']:
                raise GenerationInputRejected('context.model_context_conflict','Repair must retain the exact admitted task model context')
            if (repair.get('generation_plan_ref')!=plan['generation_plan_id']
                    or brief.get('brief_ref')!=plan['brief_ref'] or brief.get('brief_digest')!=plan['brief_digest']
                    or digest(repair.get('instruction'))!=plan['instruction_digest']):
                raise GenerationInputRejected('context.repair_task_conflict','Repair lost its exact source, Brief or child plan')
            if (digest(repair.get('candidate'))!=plan['repair_candidate_digest']
                    or digest(repair.get('repair_scope'))!=plan['repair_scope_digest']):
                raise GenerationInputRejected('context.repair_base_conflict','Repair candidate or Core scope changed after admission')
            if plan.get('repair_diagnosis_ref') and (
                    repair.get('repair_diagnosis_ref')!=plan['repair_diagnosis_ref']
                    or digest(repair.get('validation_findings'))!=plan['repair_findings_digest']):
                raise GenerationInputRejected('context.repair_diagnosis_conflict','Repair must carry its exact persisted diagnosis and findings')
            output=(text or {}).get('format') or {}
            if (output.get('type')!='json_schema' or output.get('strict') is not True
                    or digest(output.get('schema'))!=plan['output_schema_digest']):
                raise GenerationInputRejected('context.output_schema_conflict','Repair must use its exact Core-admitted grammar')
            if repair.get('repair_scope') is None and plan.get('output_locales') is not None and repair.get('output_locales')!=plan['output_locales']:
                raise GenerationInputRejected('context.output_locale_conflict','Full repair must retain the admitted locale closure')
            if tools or tool_choice not in (None,'none'):
                raise GenerationInputRejected('context.repair_tools_conflict','Repair uses complete inline guidance, without tools')
            acceptance=brief.get('task_acceptance') or {}
            if digest({key:acceptance.get(key) for key in ('facts','transitions','constraints')})!=plan['acceptance_digest']:
                raise GenerationInputRejected('context.acceptance_conflict','Repair lost the independent task assertions')
            if repair.get('write_scope')!=plan.get('write_scope'):
                raise GenerationInputRejected('context.write_scope_conflict','Repair cannot change its source-owned write scope')
            if plan.get('write_scope') and repair.get('repair_scope') is None and digest(repair.get('preservation_baseline'))!=plan['write_scope']['baseline_digest']:
                raise GenerationInputRejected('context.repair_baseline_conflict','Full fixture repair requires the exact preservation baseline')
            from .prototype_mcp import library_snapshot
            snapshot=library_snapshot()
            if len(stable)!=1 or snapshot['library_digest']!=plan['guidance_library_digest']:
                raise GenerationInputRejected('context.platform_pin_changed','Repair platform authority must match the parent plan')
            facts=stable[0].get('semantic_invariants') or {}
            for fragment in snapshot['fragments']:
                key=fragment['id']
                actual=(facts.get('ux_recommendations') or {}).get(key[3:]) if key.startswith('ux.') else facts.get(key)
                if actual is None and (key.startswith('ux.') or key in {'media','revision_scope'}):continue
                if actual is None or digest(actual)!=fragment['content_digest']:
                    raise GenerationInputRejected('context.repair_platform_dependency_missing','Repair needs complete exact inline guidance: '+key)
    envelope = {"messages": copy.deepcopy(messages), "model": model, "text": text,
                "reasoning": reasoning, "tool_choice": tool_choice, "tools": tools or []}
    return {**context_receipt(messages, generation_options=envelope), "status": "admitted",
            "transport": transport, "effective_input_digest": digest(envelope),
            "generation_plan_ref": (plan or {}).get("generation_plan_id"),
            **({'execution_origin_required':True} if (plan or {}).get('execution_origin_required') else {}),
            'parent_generation_plan_ref':(plan or {}).get('parent_generation_plan_ref'),
            'parent_request_id':(plan or {}).get('parent_request_id'),
            'required_retrieval_refs': required_retrieval_refs,
            'guidance_library_digest': (plan or {}).get('guidance_library_digest'),
            "admission_basis": "typed_envelope_and_requirement_projection",
            "semantic_acceptance": "requires_independent_candidate_and_runtime_checks"}
