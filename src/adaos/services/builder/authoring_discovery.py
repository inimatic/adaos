"""Read-only semantic discovery consumed by normal bounded Builder authoring.

Compatibility precedes ranking. Core owns local admission and task tool grants;
neither a descriptor match nor a published package makes a tool callable.
"""
from __future__ import annotations
import copy
from typing import Any, Mapping, Sequence
from adaos.domain.artifact_release import canonical_payload_digest as digest
from adaos.services.contract_context_admission import dependency_closure, discover_semantic_candidates


def installed_binding_admission(candidate, *, state_dir, application_ref, environment_profile):
    """Read exact activated local records; a retained candidate plan is not active.

    State-backed providers require their separate resolution gate. This bounded
    port never provisions identities, resolves credentials or grants tools.
    """
    from pathlib import Path
    from adaos.domain.capability_binding_state import BindingInstance
    from adaos.services.applications.cbs import ApplicationCBSService
    from adaos.services.capability_binding_state.local_state import LocalIdentityStore
    from adaos.services.builder.shared_delivery import shared_delivery_interface
    admission = ApplicationCBSService(Path(state_dir)).inspect_admission(application_ref)
    unavailable = {'status': 'unavailable', 'findings': [{'code': 'authoring.binding_unavailable'}], 'callable_tools': []}
    if not admission:
        return unavailable
    identities = LocalIdentityStore(Path(state_dir) / 'capability-binding-state/local')
    for value in admission.get('binding_instance_records', []):
        instance = BindingInstance.from_mapping(value)
        if (value['binding_definition_digest'] != candidate['binding_definition']['digest']
                or value['delivery_digest'] != candidate['delivery']['digest']):
            continue
        latest = identities.latest(instance.stable_ref, BindingInstance)
        if latest is None or latest.digest != instance.digest:
            continue
        if value['environment_profile_digest'] != environment_profile['profile_digest']:
            return {'status': 'denied', 'findings': [{'code': 'authoring.environment_incompatible'}], 'callable_tools': []}
        if candidate['state_contracts']:
            return {'status': 'insufficient', 'findings': [{'code': 'authoring.state_evidence_insufficient'}], 'callable_tools': []}
        interface = shared_delivery_interface(Path(state_dir), candidate['delivery'])
        if not interface:
            return unavailable
        skill = interface['package']['id']
        return {'status': 'admitted', 'findings': [], 'binding_instance_digest': latest.digest,
            'callable_tools': [skill + '.' + item['tool'] for item in interface['operation_bindings']]}
    return unavailable


def component_authoring_discovery(document, masks, snapshot, *, editable: bool) -> dict[str, Any]:
    views = {item['id']: item for item in document['views']}
    required = set()
    for mask in masks:
        if mask['ref'].startswith('command:'):
            required.update(('ui.actions', 'ui.form'))
        elif mask['ref'].startswith('view:'):
            view = views[mask['ref'].partition(':')[2]]
            required.add('ui.table' if view['role'] == 'collection' else 'ui.form')
            if view.get('query_controls') or any(prop.startswith('scope_filters.') for prop in mask['properties']):
                required.add('ui.queryToolbar')
        else:
            # Field labels can project into every referencing editor/collection.
            resource, _, field = mask['ref'].partition(':')[2].partition('.')
            for view in views.values():
                if view['resource_ref'] == resource and field in view.get('field_refs', []):
                    required.add('ui.table' if view['role'] == 'collection' else 'ui.form')
    closure = dependency_closure(snapshot, sorted(required))
    units = {unit['id']: unit for unit in snapshot['retrieval_units']}
    result = {'schema': 'adaos.builder.authoring_discovery.v1', 'source': snapshot['provenance'],
        'required_closure': closure, 'semantic_support': 'supported',
        'binding_availability': {'authoring': 'available' if editable else 'unavailable', 'production': 'not_assessed'},
        'callable_tools': [], 'candidates': [{'id': name, 'locator': units[name]['locator'],
            'content_digest': units[name]['content_digest']} for name in closure],
        'policy': snapshot['discovery_policy'], 'findings': [] if editable else [{'code': 'authoring.binding_unavailable'}]}
    result['discovery_digest'] = digest(result)
    return result


def qualified_semantic_discovery(query: Mapping[str, Any], *, registry_root, index, registry_revision: str,
                                 local_admit=None, task_tools: Sequence[str] = ()) -> dict[str, Any]:
    """Use the owned CBS query, then a separate Core local-admission port.

    The port must return verified local facts. This function is not exposed as
    an MCP tool and model output cannot supply local admission or tool grants.
    """
    portable = discover_semantic_candidates(query, registry_root=registry_root, index=index,
                                            registry_revision=registry_revision)['registry_result']
    rows = []
    for requirement in portable['requirements']:
        for candidate in requirement['candidates']:
            local = local_admit(candidate) if local_admit else {'status': 'unavailable', 'findings': [{'code': 'authoring.binding_unavailable'}]}
            # Even a local binding cannot widen the actual task tool allowlist.
            tools = sorted(set(local.get('callable_tools', [])) & set(task_tools)) if local.get('status') == 'admitted' else []
            rows.append({'requirement_ref': requirement['requirement_ref'], 'candidate': copy.deepcopy(candidate), 'semantic_support': 'supported',
                'binding_availability': local['status'], 'callable_tools': tools,
                'findings': copy.deepcopy(local.get('findings', []))})
    result = {'schema': 'adaos.builder.semantic_discovery_admission.v1', 'registry_result': portable,
        'candidates': rows, 'callable_tools': sorted({tool for row in rows for tool in row['callable_tools']}),
        'activation_performed': False}
    result['discovery_digest'] = digest(result)
    return result
