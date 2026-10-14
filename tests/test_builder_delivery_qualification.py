import pytest

from pathlib import Path
from adaos.services.builder.delivery_qualification import DeliveryQualificationError, qualify_message_hook, qualify_native_proposal_boundary

HOOK = '''def _builder_llm_messages(request, final_user_prompt):
    messages = [dict(role='system', content=request['system_prompt']), dict(role='user', content=request['stable_user_prompt']), dict(role='user', content=final_user_prompt)]
    purposes = ['system_policy', 'stable_context', 'user_delta']
    if os.getenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "full") != "full":
        compiler = getattr(sdk_builder_prototype, "compile_generation_messages", None)
        if not callable(compiler):
            raise RuntimeError("Current SDK required")
        messages, delivery = compiler(messages)
    return messages, purposes
'''

# Public CI has no private Workspace checkout. This independent boundary double
# tests the qualifier; live delivery executes the actual package entry point.
NATIVE = '''def _try_bounded_semantic_request(*, instruction, ws, session, binding, topic, _meta, auto_apply, output_mode):
    admission = sdk_builder_semantic_ui.admit(project_ref='scenario:scenario', instruction=instruction,
        write_properties={_meta['builder_semantic_write_ref']: [_meta['builder_semantic_write_property']]})
    prepared = sdk_builder_semantic_ui.propose(admission_id=admission['admission_id'])
    metrics = prepared.get("execution_metrics") or {}
    common = dict(model_calls=prepared.get('model_calls', 0), execution_metrics=metrics,
        semantic_admission=admission["admission_id"])
    common['findings'] = prepared.get('findings', [])
    if prepared['status'] != 'planned':
        return {**common, 'status': 'semantic_proposal_' + prepared['status']}
    if not auto_apply:
        return {**common, 'status': 'semantic_proposal_ready'}
    result = sdk_builder_semantic_ui.apply(prepared['request'], execution_metrics=metrics)
    return {**common, 'status': 'semantic_proposal_applied', 'semantic_transaction': result}
'''


def declared_source():
    path = Path(__file__).resolve().parents[1] / '.adaos/workspace/skills/builder_skill/handlers/main.py'
    return path.read_text(encoding='utf-8') if path.is_file() else NATIVE


def test_hook_gate_requires_real_opt_in_and_legacy_safe_assembly():
    result = qualify_message_hook(HOOK)
    assert result['checks'] == ['default-full', 'full', 'hybrid-focused'] and result['model_calls'] == 0


@pytest.mark.parametrize('fault', ['missing_hook', 'unconditional_compiler', 'drop_policy', 'ignore_strategy'])
def test_hook_gate_rejects_counterfactual_delivery_defects(fault):
    source = HOOK
    if fault == 'missing_hook': source = 'def unrelated(): pass\n'
    elif fault == 'unconditional_compiler': source = source.replace('if os.getenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "full") != "full":', 'if True:')
    elif fault == 'drop_policy': source = source.replace('return messages, purposes', 'return messages[1:], purposes[1:]')
    else: source = source.replace('!= "full"', '== "unsupported"')
    with pytest.raises(DeliveryQualificationError):
        qualify_message_hook(source)


def test_legacy_synthetic_sdk_index_is_not_claimed_as_root_mcp_retrieval(monkeypatch):
    from adaos.e2e import context_ablation
    monkeypatch.setattr(context_ablation, '_implementation_sdk_contract_bundle', lambda: {'contracts': [{'name': 'sdk.example', 'signature': 'example()'}]})
    evidence = context_ablation.fixed_contract_index()['evidence']
    assert evidence['source_scope'] == 'local_sdk_export_snapshot'
    assert evidence['root_mcp_readable'] is False


def test_native_gate_executes_all_outcomes_in_the_declared_source(tmp_path):
    source = declared_source()
    receipt = qualify_native_proposal_boundary(source, scratch_root=tmp_path)
    assert len(receipt['checks']) == 5 and receipt['model_calls'] == 0


def test_native_gate_detects_the_previously_shipped_stale_contract_defect(tmp_path):
    source = declared_source()
    mutant = source.replace('semantic_admission=admission["admission_id"])',
                            'root_job_id=prepared["root_job_id"], semantic_admission=admission["admission_id"])', 1)
    with pytest.raises(DeliveryQualificationError, match='native_outcome_lost:stale_contract'):
        qualify_native_proposal_boundary(mutant, scratch_root=tmp_path)
