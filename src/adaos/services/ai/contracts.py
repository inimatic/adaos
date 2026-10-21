"""Portable CBS seam; private sessions and admission stay in Core.

M1 ships one built-in binding. Semantic package selection of alternative
providers is intentionally not a runtime dependency of personal ChatGPT.
"""
from adaos.domain.capability_binding_state import BindingDefinition, CapabilityContract


def _object(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required),
            'additionalProperties': False}


def operation_schemas():
    """Portable SDK operation shapes; no account/token or consumer authority input."""
    job_id = {'type': 'string', 'minLength': 1}
    submission = _object({
        'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'resource_ref': {'type': 'string', 'minLength': 1},
        'model': {'type': 'string', 'minLength': 1},
        'expected_binding': {'type': 'string', 'minLength': 1},
        'inputs': {'type': 'array', 'items': _object({
            'role': {'enum': ['user', 'assistant', 'developer']},
            'content': {'type': 'string'}}, ('role', 'content'))},
        'instructions': {'type': 'string'}, 'web_search': {'type': 'boolean'},
        'reasoning_effort': {'type': 'string'}, 'text': {'type': 'object'}},
        ('request_id', 'resource_ref', 'model', 'expected_binding', 'inputs'))
    attempt = {'type': 'object', 'required': ['job_id', 'status', 'output_text'],
        'properties': {'job_id': job_id,
            'status': {'enum': ['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted']},
            'output_text': {'type': 'string'}, 'usage': {'type': 'object'},
            'citations': {'type': 'array'}, 'error': {'type': ['string', 'null']}}}
    return {
        'models': (_object({'refresh': {'type': 'boolean'}}),
                   _object({'models': {'type': 'array', 'items': {'type': 'object'}}}, ('models',))),
        'submit': (submission, attempt),
        'observe': (_object({'job_id': job_id}, ('job_id',)), attempt),
        'cancel': (_object({'job_id': job_id}, ('job_id',)), attempt),
    }


def inference_contract():
    return CapabilityContract.create(capability_ref='capability:ai.inference', version='1.0.0',
        title='Personal AI inference', operations=tuple({'operation_id': op,
            'input_schema': schemas[0], 'output_schema': schemas[1],
            'errors': ['not_authorized', 'connection_changed', 'provider_unavailable', 'idempotency_conflict']}
            for op, schemas in operation_schemas().items()),
        invariants=('credentials-remain-in-core', 'explicit-resource-and-model', 'no-implicit-paid-fallback'),
        effects=('external-inference', 'personal-subscription-usage'),
        authority_requirements=('ai.inference', 'resource-owner'), dependencies=(), state_ports=(),
        conformance_refs=('ai-personal-foreground-v1',))


def chatgpt_binding():
    contract = inference_contract()
    return BindingDefinition.create(binding_definition_ref='binding-definition:ai.inference.openai-chatgpt',
        version='1.0.0', capability_ref=contract.capability_ref, capability_version=contract.version,
        entry_protocol='adaos-sdk-ai-v1', implementation_entrypoint='ai.openai-chatgpt',
        state_support=(), modes=('production',), environment_constraints={'profile_classes': ['local'], 'provider_features': ['text-streaming', 'personal-subscription']},
        authority_requirements=('ai.inference', 'resource-owner'),
        conformance_obligations=('ai-personal-foreground-v1',))


def install_contracts(state_dir):
    """Index Core's built-in semantics in the existing portable CBS catalog.

    No package delivery/evidence, delegation, or activated BindingInstance is
    fabricated. A future provider package must pass normal CBS admission.
    """
    from pathlib import Path
    from adaos.services.capability_binding_state.catalog import PortableContractCatalog
    catalog = PortableContractCatalog(Path(state_dir) / 'capability-binding-state' / 'portable')
    catalog.put_many((inference_contract(), chatgpt_binding()))


def describe_capability():
    return {'contract': inference_contract().to_dict(), 'binding': chatgpt_binding().to_dict(),
        'implementation': 'core_builtin', 'sdk_module': 'adaos.sdk.ai',
        'management_application': 'subscription_status', 'consumer_depends_on_management_app': False,
        'required_skill_capabilities': ['ai.inference'],
        'project_requirement': 'capability:ai.inference@^1.0.0',
        'cbs_intent': {'schema': 'adaos.builder.cbs_intent.v1', 'requirements': [{
            'id': 'personal-ai', 'capability_ref': 'capability:ai.inference',
            'contract_range': '^1.0.0', 'origin': 'human_explicit', 'locality': 'local_only',
            'privacy': 'owner-authorized-external-inference', 'required_authorities': ['ai.inference']}]},
        'portable_provider_activation': 'requires_delivery_and_evidence',
        'authority': 'Verified resource owner plus declaring skill; CBS never grants subscription access',
        'workflow': ['status: require plan_enabled and capture binding_ref',
            'models: select an advertised model explicitly',
            'submit: stable request_id and expected_binding; no provider retry or paid fallback',
            'job: observe the same attempt after a foreground timeout', 'cancel: explicit cancellation'],
        'out_of_scope': ['automation', 'family_delegation', 'alternative_provider_activation']}
