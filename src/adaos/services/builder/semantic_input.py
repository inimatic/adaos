"""Versioned authoring authority for isolated semantic generation.

This supplements (never replaces) the existing grammar, task and guidance.
It is deliberately opt-in until its complete composed input is qualified.
"""
from __future__ import annotations

import copy
import json

from adaos.services.builder_context import canonical, digest

PROFILE = 'semantic-input-v1'
CAPABILITY_SCOPE = {
    'kind': 'renderer_integration_advice',
    'identity_namespace': 'ABI renderer/recipe IDs, not candidate resource/view/field IDs.',
    'postconditions_owner': 'Core compiler and component/Automation integration. NOT extra Prototype '
        'output fields, task acceptance requirements or a mandate to generate handlers/agent schemas.',
    'availability': 'Selection does not prove semantic reachability, complete coverage or runtime conformance. '
        'Use the semantic grammar/invariants for authorable constructions; do not infer a semantic property '
        'from an ABI component property. These items are not an allowlist.',
}


def schema_nodes(node, pointer='#'):
    """Walk schemas, never property maps or arbitrary examples as schemas."""
    if not isinstance(node, dict):
        return
    yield pointer, node
    for key, value in node.items():
        child = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
        if key in {'$defs', 'definitions', 'properties', 'patternProperties', 'dependentSchemas'} and isinstance(value, dict):
            for name, descriptor in value.items():
                yield from schema_nodes(descriptor, child + '/' + name.replace('~', '~0').replace('/', '~1'))
        elif key in {'allOf', 'anyOf', 'oneOf', 'prefixItems'} and isinstance(value, list):
            for index, descriptor in enumerate(value):
                yield from schema_nodes(descriptor, child + '/' + str(index))
        elif key in {'items', 'additionalProperties', 'contains', 'not', 'if', 'then', 'else', 'propertyNames'}:
            yield from schema_nodes(value, child)


def authoring_contract():
    from .task_acceptance import display_assertion_contract
    from .semantic_owner_references import owner_reference_contract
    from .semantic_prototype import semantic_prototype_candidate_contract
    from .input_composition import PLATFORM_REFERENCE_SCOPE
    from .operation_contract import operation_contract
    from .interpretation_boundary import contract as boundary_contract

    schema = semantic_prototype_candidate_contract(version='v2')
    annotations = {}

    for pointer, node in schema_nodes(schema):
        notes = {key: copy.deepcopy(node[key]) for key in
                 ('description', 'title', '$comment', 'default', 'examples') if key in node}
        if notes:
            annotations[pointer] = {**({'ref': node['$ref']} if '$ref' in node else {}), **notes}
    return {
        'schema': 'adaos.builder.semantic_authoring_authority.v1',
        'profile': PROFILE,
        'product_operations': operation_contract(),
        'requirement_boundary': boundary_contract(),
        'scope': 'Semantic Prototype authoring, not runtime ABI authoring or Automation implementation.',
        'authorities': {
            'grammar': 'The actual strict text.format.schema is the output shape for THIS call. '
                'All required keys must be present. Null is legal only where that schema permits it. '
                'Core authoring_constraints and semantic invariants also apply; strict JSON alone is not semantic validity.',
            'task': 'builder_request (or semantic_repair) contains the source instruction and Core-admitted '
                'prototype_brief. Its task_acceptance facts/transitions/constraints and exact required_references '
                'are obligations, not illustrative suggestions. Preserve their identity, values and relationships. '
                'Process/authoring instructions do not require invented application entities.',
            'baseline': 'current_semantic and any write_scope constrain a revision; they are not permission '
                'to redesign unrelated content. A repair candidate is evidence to correct, not new task authority. '
                'Core narrows each source fact assertion binding to its exact canonical field after value admission. '
                'Retain that field identity when changing labels/locales; do not relocate a source fact to another field. '
                'baseline_source_field_bindings below locates existing source fields; it never supplies expected values. '
                'Source assertion bindings are Core input metadata, not extra UI requirements to emit. '
                'For new fields use an unambiguous source field ID or source-name label; Core retains the exact locator '
                'after value admission. Candidate requirement_bindings still use the actual provider schema enum.',
            'platform': 'stable_builder_context.semantic_invariants defines semantic behavior and bounds. '
                'platform_contracts describes Core-owned rendering/runtime capabilities, not additional candidate fields.',
            'recommendations': 'semantic_capability_context items are selected implementation advice, NOT '
                'an allowlist or extra user requirements. Their postconditions describe component/recipe integration '
                'responsibilities; do not emit handlers, ABI component trees or extra application features to satisfy them. '
                'Required bindings/assets still need independent Core admission.',
        },
        'design_freedom': 'Choose valid layouts, presentations, labels and fixture details when the task leaves '
            'them open. Prefer the simplest design satisfying the requested workflow; multiple designs can be valid. '
            'Do not replace explicit source facts with a plausible alternative. A missing external integration '
            'is not a locally implemented effect: preserve its requirement and disclose the supported deferred behavior.',
        'references': owner_reference_contract(),
        'display_assertions': display_assertion_contract(),
        'reference_representation': 'For fresh output use owner-local fulfills on the actual evidence-bearing element. '
            'Core binds its containing ID; a source_transition also binds its admitted transition_ref. '
            'For a requirement implemented by several elements, repeat its SAME ref on EACH actual owner. '
            'Core merges these into one binding; no content/section surrogate view is needed. '
            'If this call sets requirement_bindings maxItems=0, keep it empty; legacy explicit output '
            'remains supported only when its actual schema allows it. All claims undergo independent acceptance. '
            'current_semantic.document and Core field locators are canonical INPUT, '
            'not a provider-shaped candidate to copy verbatim. A canonical locator field:display_name becomes '
            '{"kind":"field","id":"display_name"} in a provider semantic_refs item. Use the actual declared '
            'field.id unchanged; do not prepend a resource ID unless that prefix is already part of field.id. '
            'Graph-internal owner-qualified node identities are not an additional provider reference namespace.',
        'capability_advice_scope': copy.deepcopy(CAPABILITY_SCOPE),
        'instruction_composition': 'Core pins instruction contents, roles, order and platform headers before submission. '
            'The entire selected semantic_invariants and prototype_retrieval projection is pinned, including '
            'nested fields, presence and omissions; undeclared guidance keys are rejected against the canonical library. '
            'No additional system/developer instruction or assistant-role task/platform carrier is admitted. '
            'Only the independently admitted trailing C2 platform reference may be added during normalization.',
        'platform_reference_scope': PLATFORM_REFERENCE_SCOPE,
        'schema_annotations': {
            'source': 'Canonical v2 authoring schema. JSON pointers address that schema, not a candidate. '
                'Role-specific provider branches may narrow these properties; the actual output schema still controls shape.',
            'projection': 'missing-annotations-v1: repeat only explanations for extant schema subjects that '
                'are absent from all matching provider branches. Intentional role narrowing is not missing information. '
                'Scoped repairs receive no annotations for absent full-candidate subjects.',
            'canonical_annotations': annotations,
        },
        'examples': {
            'scope': 'Independent partial illustrations, NOT a complete candidate or extra task facts. '
                'Use task-owned identifiers/values in the actual candidate.',
            'owner_chain': {'resources': [{'id': 'servers'}],
                'views': [{'id': 'server_editor', 'resource_ref': 'servers'}],
                'commands': [{'id': 'save_server', 'view_ref': 'server_editor'}]},
            'positional_record': {'ordered_fields': [
                {'id': 'host', 'value_type': 'short_text'},
                {'id': 'cpu', 'value_type': 'number'},
                {'id': 'enabled', 'value_type': 'boolean'}],
                'record': {'id': 'server_a', 'values': ['example-host', 45, True]}},
            'section_and_view': {'view': {'id':'items_table',
                'section':{'id':'summary','kind':'tab','title':{'en':'Summary','ru':'Сводка'}},
                'title':{'en':'Example data','ru':'Пример данных'}},
                'provider_reference':{'kind':'view','id':'items_table'},
                'explanation':'Partial view only: Summary is the section name, Example data is separate '
                    'visible explanatory text. References use items_table, not summary or an invented content view. '
                    'Use the task-owned section names/disclosures, actual declared view ids and ONLY the '
                    'locales required by this call schema in your candidate.'},
        },
        'acceptance': 'Return the requested structured output only. Core checks grammar, references, '
            'task evidence and preservation; executable/browser checks establish behavior separately. '
            'A requirement_binding or a plausible screenshot is not proof of a working interaction.',
    }


def input_contract_pin(profile):
    if profile != PROFILE:
        raise ValueError('Unknown semantic input profile')
    return {'profile': profile, 'authority_digest': digest(authoring_contract())}


def _reject(code, detail):
    from .generation_plan import GenerationInputRejected
    raise GenerationInputRejected('context.' + code, detail)


def _schema_targets(node, tokens):
    """Resolve canonical pointers through transparent provider role variants."""
    if not tokens:
        return [node] if isinstance(node, dict) else []
    if isinstance(node, dict):
        if tokens[0] in node:
            return _schema_targets(node[tokens[0]], tokens[1:])
        return [target for branch in node.get('anyOf', []) for target in _schema_targets(branch, tokens)]
    if isinstance(node, list) and tokens[0].isdigit() and int(tokens[0]) < len(node):
        return _schema_targets(node[int(tokens[0])], tokens[1:])
    return []


def _contract(plan, text):
    pin = plan.get('semantic_input_contract')
    if not isinstance(pin, dict) or pin.get('profile') != PROFILE or pin != input_contract_pin(PROFILE):
        _reject('authoring_pin_changed', 'The authoring authority changed after planning; re-admit the input explicitly')
    output = (text or {}).get('format') or {}
    if output.get('type') != 'json_schema' or output.get('strict') is not True or not output.get('schema'):
        _reject('authoring_output_missing', 'The isolated authoring contract needs the actual strict output schema')
    authority = authoring_contract()
    if plan.get('source_field_bindings'):
        from .source_field_bindings import validate_binding_context
        try:
            authority['baseline_source_field_bindings']=copy.deepcopy(validate_binding_context(plan['source_field_bindings']))
        except ValueError:
            _reject('source_field_binding_conflict','The planned baseline locator projection contains undeclared or ambiguous fields')
    annotations = authority['schema_annotations'].pop('canonical_annotations')
    missing = {}
    discriminator = output['schema'].get('properties', {}).get('schema', {})
    full_candidate = (discriminator.get('const') == 'adaos.builder.semantic_prototype_candidate.v2'
        or discriminator.get('enum') == ['adaos.builder.semantic_prototype_candidate.v2'])
    for pointer, notes in annotations.items():
        if pointer == '#' and not full_candidate:
            continue  # A patch root is not the full-candidate schema subject.
        tokens = [part.replace('~1', '/').replace('~0', '~') for part in pointer.removeprefix('#/').split('/')] if pointer != '#' else []
        targets = _schema_targets(output['schema'], tokens)
        prose = {key: value for key, value in notes.items() if key != 'ref'}
        # Role narrowing legitimately removes inapplicable properties. Restore
        # annotations only for an extant subject, and only if no branch already
        # supplies that same explanation. Never reprint the whole schema manual.
        if targets and not any(all(target.get(key) == value for key, value in prose.items()) for target in targets):
            missing[pointer] = notes
    authority['schema_annotations'].update(canonical_annotations_digest=digest(annotations),
        annotations_missing_from_output_grammar=missing)
    return {**authority, 'call': {
        'stage': plan.get('context_stage', 'generation'),
        'output_schema_digest': digest(output['schema']),
        'repair_scope': 'For repair use ONLY the child plan output grammar and repair_scope; '
            'the full authoring language below does not authorize a broader patch or redesign.'
            if plan.get('context_stage') == 'repair' else None}}


def _carriers(messages):
    result = []
    for index, row in enumerate(messages):
        try:
            body = json.loads(row.get('content', ''))
        except (ValueError, TypeError):
            continue
        if isinstance(body, dict) and 'stable_builder_context' in body:
            if not isinstance(body['stable_builder_context'], dict):
                _reject('authoring_carrier_conflict', 'Platform carrier must be an object')
            result.append((index, body))
        if isinstance(body, dict) and 'semantic_authoring_authority' in body:
            _reject('authoring_carrier_conflict', 'Authoring authority belongs only inside the single platform carrier')
    return result


def _capability_carriers(messages, plan, *, require_scope):
    carriers = []
    for index, row in enumerate(messages):
        try:
            body = json.loads(row.get('content', ''))
        except (ValueError, TypeError):
            continue
        if not isinstance(body, dict) or 'semantic_capability_context' not in body:
            continue
        context = body['semantic_capability_context']
        if not isinstance(context, dict):
            _reject('capability_advice_conflict', 'Capability advice must be an object')
        raw = copy.deepcopy(context)
        scope = raw.pop('authoring_scope', None)
        if (scope is not None and scope != CAPABILITY_SCOPE) or (require_scope and scope != CAPABILITY_SCOPE):
            _reject('capability_advice_conflict', 'Renderer advice lost or changed its explicit authority scope')
        carriers.append((index, body, raw))
    expected = plan.get('capability_context_digest')
    if (len(carriers) != (1 if expected else 0)
            or carriers and digest(carriers[0][2]) != expected):
        _reject('capability_advice_conflict', 'Selected capability advice changed or was duplicated after planning')
    return carriers


def compose_semantic_input(messages, *, plan=None, text=None):
    """Compile once on the common provider rail; legacy input is byte-preserved."""
    if not (plan or {}).get('semantic_input_contract'):
        return messages
    from .input_composition import check_composition
    check_composition(messages, plan)
    carriers = _carriers(messages)
    if len(carriers) != 1:
        _reject('authoring_carrier_conflict', 'Exactly one platform carrier is required')
    expected = _contract(plan, text)
    index, body = carriers[0]
    supplied = body['stable_builder_context'].get('semantic_authoring_authority')
    if supplied is not None and supplied != expected:
        _reject('authoring_contract_conflict', 'Do not silently overwrite a different authoring authority')
    result = copy.deepcopy(messages)
    body['stable_builder_context']['semantic_authoring_authority'] = expected
    result[index]['content'] = canonical(body)
    for index, body, _ in _capability_carriers(messages, plan, require_scope=False):
        body['semantic_capability_context']['authoring_scope'] = copy.deepcopy(CAPABILITY_SCOPE)
        result[index]['content'] = canonical(body)
    return result


def admit_semantic_input(messages, *, plan, text):
    """Check the exact post-adapter authority, not just a precomposition flag."""
    if not (plan or {}).get('semantic_input_contract'):
        return None
    from .input_composition import check_composition
    check_composition(messages, plan)
    carriers = _carriers(messages)
    if len(carriers) != 1:
        _reject('authoring_carrier_conflict', 'Exactly one platform carrier is required')
    expected = _contract(plan, text)
    if carriers[0][1]['stable_builder_context'].get('semantic_authoring_authority') != expected:
        _reject('authoring_contract_conflict', 'The actual isolated model input lost or changed its authoring authority')
    _capability_carriers(messages, plan, require_scope=True)
    return {'profile': PROFILE, 'authority_digest': plan['semantic_input_contract']['authority_digest'],
            'delivered_digest': digest(expected), 'output_schema_digest': expected['call']['output_schema_digest'],
            'capability_context_digest': plan.get('capability_context_digest')}
