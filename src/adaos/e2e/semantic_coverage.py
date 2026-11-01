"""Read-only, model-free semantic coverage evidence on the Builder E2E rail.

Witnesses establish reachability of named constructions, not complete support of
a renderer's inputs, semantic equivalence, or model understanding. Missing
witnesses are unknown, never silently classified as unsupported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _hash(value):
    from adaos.services.builder_context import digest
    return digest(value)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _text(value):
    return {'en': value, 'ru': value}


def inspect_recorded_request(record, library):
    """Inspect exact saved inputs without copying prompt text or credentials."""
    observed = {}
    for message in record['messages']:
        content = message.get('content')
        if not isinstance(content, str):
            continue
        try:
            body = json.loads(content)
        except ValueError:
            continue
        if not isinstance(body, dict):
            continue
        guidance = body.get('stable_builder_context', {}).get('semantic_invariants', {})
        for key, value in guidance.items():
            if key == 'contract':
                continue
            if key == 'ux_recommendations':
                observed.update({'ux.' + child: _hash(data) for child, data in value.items()})
            else:
                observed[key] = _hash(value)
    expected = {row['id']: row['content_digest'] for row in library['fragments']}
    schema = record.get('generation', {}).get('options', {}).get('text', {}).get('format', {}).get('schema')
    return {'scope': 'saved_Builder_request_not_independent_transport_observation',
        'messages_digest': _hash(record['messages']), 'guidance_fragments_present': sorted(set(observed) & set(expected)),
        'guidance_fragments_absent': sorted(set(expected) - set(observed)),
        'guidance_fragments_changed': sorted(k for k in observed.keys() & expected.keys() if observed[k] != expected[k]),
        'unknown_fragment_count': len(set(observed) - set(expected)),
        'provider_schema_digest': _hash(schema) if schema else None,
        'understanding': 'Presence is not comprehension; original failures and repair still count.'}


def inventory_freshness(client_root, inventory):
    client_root = Path(client_root).resolve()
    stale = []
    for source in inventory.get('generated_from', {}).get('sources', []):
        path = (client_root / source['path']).resolve()
        if not path.is_relative_to(client_root):
            raise ValueError('Client inventory source escapes its owner')
        # The Client generator hashes canonical LF text, not checkout bytes.
        if not path.is_file() or hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest() != source['sha256']:
            stale.append(source['path'])
    return {'status': 'stale' if stale else 'source_hashes_match', 'stale_sources': stale}


def candidate_fixture():
    """Directly authored provider-shaped fixture, NOT a reverse compiler."""
    def field(identity, kind, *, required=False, options=()):
        return {'id': identity, 'label': _text(identity), 'value_type': kind,
            'required': required, 'editable': True, 'options': list(options),
            'visible_when': None, 'help_text': None, 'placeholder': None}

    def view(identity, role, refs):
        return {'id': identity, 'resource_ref': 'items', 'role': role,
            'region_role': 'primary' if role == 'collection' else 'supporting',
            'title': _text(identity), 'field_refs': refs, 'surface': 'inline',
            'presentation': 'table' if role == 'collection' else None,
            'media': None, 'compact_presentation': None, 'presentation_options': None,
            'field_display': [], 'section': None, 'scope_filters': [],
            'selection': {'mode': 'single', 'indicator': 'radio', 'row_activation': 'select'} if role == 'collection' else None,
            'initial_selection_id': None, 'selection_filter': None, 'activation_source_view_ref': None,
            'filter': None, 'query_controls': [], 'empty_state': None}

    return {'schema': 'adaos.builder.semantic_prototype_candidate.v2',
        'document_id': 'semantic-coverage', 'title': _text('Coverage fixture'), 'layout': 'split',
        'resources': [{'id': 'items', 'item_semantics': 'One local synthetic item.',
            'item_label': _text('Item'), 'read_only_when': None,
            'fields': [field('name', 'short_text', required=True),
                field('state', 'choice', required=True, options=[{'value': v, 'label': _text(v)} for v in ('open', 'done')]),
                field('amount', 'number'), field('parent', 'short_text'),
                field('asset', 'attachment'), field('note', 'markdown')],
            'records': [{'id': 'item-1', 'values': ['First', 'open', 3, None, 'sample://image', '**First**']},
                        {'id': 'item-2', 'values': ['Second', 'done', 7, 'item-1', None, '**Second**']}]}],
        'relationships': [], 'views': [view('items-list', 'collection', ['name', 'state', 'amount']),
            view('item-details', 'details', ['name', 'state', 'amount', 'asset', 'note']),
            view('item-editor', 'editor', ['name', 'state'])],
        'commands': [{'id': 'save', 'kind': 'update', 'view_ref': 'item-editor',
            'label': _text('Save'), 'confirmation': None, 'input_field_refs': ['name', 'state'],
            'fixed_values': [], 'guard': None, 'exposure': {'placement': 'editor_primary', 'presentation': 'button'}}],
        'capability_surfaces': [], 'representative_states': [], 'requirement_bindings': [],
        'capability_gaps': [], 'automation_requirements': []}


def witnesses():
    expected = {'list': 'ui.list', 'table': 'ui.table', 'cards': 'ui.list',
        'board': 'collection.board', 'tree': 'collection.tree',
        'chart': 'visual.metricChart', 'accordion': 'ui.list'}
    for presentation, widget in expected.items():
        candidate = candidate_fixture()
        view = candidate['views'][0]
        view['presentation'] = presentation
        if presentation in {'board', 'accordion', 'chart', 'tree'}:
            options = {'group_field_ref': None, 'parent_field_ref': None,
                'value_field_ref': None, 'order_field_ref': None, 'draggable': False}
            if presentation in {'board', 'accordion'}:
                options['group_field_ref'] = 'state'
            elif presentation == 'chart':
                options.update(group_field_ref='name', value_field_ref='amount')
                view['field_refs'] = ['name', 'amount']
            else:
                options['parent_field_ref'] = 'parent'
                view['field_refs'] = ['name', 'parent']
            view['presentation_options'] = options
        yield {'id': 'presentation.' + presentation, 'candidate': candidate,
            'semantic_path': '$.views[0].presentation', 'expected_widget': widget,
            'fragments': ['view_roles', 'ux.collection_presentations']}
    for surface in ('inline', 'modal', 'side_sheet'):
        candidate = candidate_fixture()
        candidate['views'][2]['surface'] = surface
        yield {'id': 'editor.' + surface, 'candidate': candidate,
            'semantic_path': '$.views[2].surface', 'expected_widget': 'ui.form',
            'fragments': ['command_ownership', 'ux.editor_surface']}
    candidate = candidate_fixture()
    candidate['views'][0]['query_controls'] = [{'id': 'by-name', 'kind': 'search', 'label': _text('Search'), 'field_ref': 'name'}]
    candidate['views'][0]['compact_presentation'] = 'cards'
    candidate['views'][1]['media'] = {'source_field_ref': 'asset', 'kind_field_ref': None, 'poster_field_ref': None}
    yield {'id': 'query.media.compact', 'candidate': candidate,
        'semantic_path': '$.views', 'expected_widget': 'ui.queryToolbar',
        'fragments': ['query_filters', 'media', 'ux.collection_presentations']}
    # Controls are nested inside ui.form, not top-level input.* renderers.
    fields = [('short_text', 'shortText', 'Text'), ('long_text', 'longText', 'Long text'),
        ('markdown', 'longText', '**Text**'), ('number', 'number', 3),
        ('date', 'date', '2026-10-31'), ('boolean', 'boolean', True),
        ('choice', 'singleChoice', 'open'), ('multi_choice', 'multiChoice', ['open']),
        ('attachment', 'fileUpload', None), ('attachments', 'fileUpload', [])]
    for kind, control, value in fields:
        candidate = candidate_fixture()
        probe = copy.deepcopy(candidate['resources'][0]['fields'][0])
        probe.update(id='probe', label=_text('Probe'), value_type=kind, required=False,
            options=copy.deepcopy(candidate['resources'][0]['fields'][1]['options']) if kind in {'choice', 'multi_choice'} else [])
        candidate['resources'][0]['fields'].append(probe)
        for record in candidate['resources'][0]['records']:
            record['values'].append(value)
        candidate['views'][2]['field_refs'].append('probe')
        candidate['commands'][0]['input_field_refs'].append('probe')
        yield {'id': 'field.' + kind, 'candidate': candidate,
            'semantic_path': '$.resources[0].fields[-1]', 'expected_widget': 'ui.form',
            'expected_control': {'id': 'probe', 'type': control,
                **({'multiple': kind == 'attachments'} if control == 'fileUpload' else {})},
            'fragments': ['modeling']}
    for kind in ('tab', 'settings'):
        candidate = candidate_fixture()
        candidate['views'][0]['section'] = {'id': 'queue', 'kind': 'tab', 'title': _text('Queue')}
        candidate['views'][1]['section'] = {'id': 'info', 'kind': kind, 'title': _text('Info')}
        yield {'id': 'section.' + kind, 'candidate': candidate,
            'semantic_path': '$.views[*].section', 'expected_widget': 'ui.actions',
            'expected_section': kind, 'fragments': ['ux.sections']}
    candidate = candidate_fixture()
    candidate['capability_surfaces'] = [{'id': 'site', 'capability_ref': 'visual.sitePreview',
        'region_role': 'primary', 'title': _text('Site'), 'site_id': 'coverage-proof', 'route': '/', 'min_height_px': 480}]
    yield {'id': 'surface.sitePreview', 'candidate': candidate,
        'semantic_path': '$.capability_surfaces[0]', 'expected_widget': 'visual.sitePreview',
        'fragments': ['modeling']}


def construction_checks(fixture, webui):
    """Compare emitted structure with named, independently specified obligations."""
    application = webui['ui']['application']
    nodes = list(_walk(webui))
    expected = fixture.get('expected_control')
    result = {}
    if expected:
        form = next(row for row in nodes if row.get('id') == 'item-editor' and row.get('type') == 'ui.form')
        control = next(row for row in form['inputs']['fields'] if row['id'] == expected['id'])
        actual = {key: control.get(key, False) if key == 'multiple' else control.get(key) for key in expected}
        if actual != expected:
            raise ValueError(f'Field control obligation mismatch: {actual!r} != {expected!r}')
        result['control'] = actual
    if fixture.get('expected_section'):
        page = application['desktop']['pageSchema']
        navigation = next(row for row in page['widgets'] if row['id'] == 'prototype-sections')
        if navigation['inputs']['variant'] != 'tabs' or page['initialState']['prototype_section'] != 'queue':
            raise ValueError('Section navigation/default selection mismatch')
        result['navigation'] = 'ui.actions:tabs'
        if fixture['expected_section'] == 'settings':
            modal = application['modals']['settings-info']
            if not any(row['id'] == 'item-details' for row in modal['schema']['widgets']):
                raise ValueError('Settings lost owned details content')
            opener = next(row for row in page['widgets'] if row['id'] == 'prototype-settings')['actions'][0]
            if opener['type'] != 'navigateModal' or opener['params'] != {'modalId': 'settings-info'}:
                raise ValueError('Settings opener is not an owned local modal address')
            result['settings'] = 'navigateModal:settings-info'
    return result


def audit(repo_root, *, recorded_request_ref=None):
    from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate, semantic_prototype_provider_contract, semantic_prototype_generation_guidance
    from adaos.services.builder.prototype_mcp import library_snapshot
    from adaos.services.builder.prototype_context import prototype_requirement_inventory
    from adaos.services.builder.semantic_owner_references import owner_reference_contract
    from adaos.services.builder_intent import compile_prototype_brief
    from adaos.services.ui_capabilities import ui_capability_catalog

    root = Path(repo_root).resolve()
    abi = _read(root / 'src/adaos/abi/webui.v1.schema.json')
    semantic = _read(root / 'src/adaos/abi/webui.semantic.v2.schema.json')
    inventory_path = root / 'src/adaos/integrations/adaos-client/architecture/evidence/client-capability-inventory.v1.json'
    inventory = _read(inventory_path)
    catalog = ui_capability_catalog(domain_packs=[])
    provider = semantic_prototype_provider_contract(version='v2')
    guidance = semantic_prototype_generation_guidance()
    library = library_snapshot()
    fragments = {row['id']: row for row in library['fragments']}
    recorded_input = None
    if recorded_request_ref:
        request_path = (root / recorded_request_ref).resolve()
        if not request_path.is_relative_to((root / 'e2e/artifacts/builder').resolve()):
            raise ValueError('Recorded request must be retained Builder evidence')
        recorded_input = {**inspect_recorded_request(_read(request_path), library),
            'source_ref': recorded_request_ref, 'source_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest()}
    brief = compile_prototype_brief('Show local records.')
    widget_types = set(abi['$defs']['widgetType']['enum'])
    runtime_widgets = {row['type']: row for row in inventory['widgets']}
    catalog_widgets = {row['manifest']['widget_type']: row for row in catalog['components']}
    runtime_actions = {row['type'] for row in inventory['actions']}
    runtime_sources = {row['kind'] for row in inventory['data_sources']}
    cases = []
    observed = {'widget': {}, 'action': {}, 'data_source': {}}
    for fixture in witnesses():
        candidate = copy.deepcopy(fixture['candidate'])
        candidate['requirement_bindings'] = [{'requirement_ref': row['id'],
            'semantic_refs': [{'kind': 'view', 'id': 'items-list'}]}
            for row in prototype_requirement_inventory(brief)]
        case = {key: value for key, value in fixture.items() if key != 'candidate'}
        case.update(candidate_digest=_hash(candidate), provider_schema_accepted=False,
            lowering_accepted=False, runtime_behavior='not_executed', model_understanding='not_measured')
        try:
            Draft202012Validator(provider).validate(candidate)
            case['provider_schema_accepted'] = True
            compiled = compile_semantic_prototype_candidate(candidate, brief=brief, project_ref='project:semantic-coverage')
            nodes = list(_walk(compiled['webui']))
            kinds = {'widget': sorted({row['type'] for row in nodes if isinstance(row.get('type'), str) and row['type'] in widget_types}),
                'action': sorted({row['type'] for row in nodes if isinstance(row.get('type'), str) and row['type'] in runtime_actions}),
                'data_source': sorted({row['kind'] for row in nodes if isinstance(row.get('kind'), str) and row['kind'] in runtime_sources})}
            if fixture['expected_widget'] not in kinds['widget']:
                raise ValueError('Expected renderer was not emitted: ' + fixture['expected_widget'])
            checks = construction_checks(fixture, compiled['webui'])
            from adaos.services.interface_deprecations import validate_authoring_interfaces
            from adaos.services.webui_contract import validate_application_ui_contract
            if not validate_authoring_interfaces(compiled['webui'])['ok']:
                raise ValueError('Compiler emitted a migration-only interface')
            errors = [row.to_dict() for row in validate_application_ui_contract(compiled['webui']['ui']['application']) if row.level == 'error']
            if errors:
                raise ValueError('Application contract admission failed: ' + str(errors))
            for identity in fixture['fragments']:
                expected = guidance['ux_recommendations'][identity[3:]] if identity.startswith('ux.') else guidance[identity]
                if fragments[identity]['content'] != expected:
                    raise ValueError('Guidance/retrieval fragment drift: ' + identity)
            case.update(lowering_accepted=True, emitted=kinds, webui_digest=_hash(compiled['webui']),
                construction_checks=checks,
                semantic_digest=compiled['semantic_digest'], source_map=compiled['source_map'],
                guidance_fragments={identity: fragments[identity]['content_digest'] for identity in fixture['fragments']})
            for family, ids in kinds.items():
                for identity in ids:
                    observed[family].setdefault(identity, []).append(fixture['id'])
        except Exception as exc:
            case['error'] = type(exc).__name__ + ': ' + str(exc)
        cases.append(case)

    rows = []
    for identity in sorted(widget_types | set(runtime_widgets) | set(catalog_widgets)):
        profile = catalog_widgets.get(identity)
        rows.append({'id': identity, 'abi_declared': identity in widget_types,
            'client_registration': runtime_widgets.get(identity),
            'catalog_profile': ('minimal' if profile.get('semantic_profile') == 'minimal' else 'curated') if profile else 'absent',
            'semantic_lowering': 'witnessed' if identity in observed['widget'] else 'not_witnessed',
            'witnesses': observed['widget'].get(identity, []),
            'coverage_scope': 'renderer_type_presence_only_not_all_inputs_or_compositions',
            'runtime_behavior': 'not_executed', 'actual_llm_wire': 'not_observed'})
    source_paths = ['src/adaos/abi/webui.v1.schema.json', 'src/adaos/abi/webui.semantic.v2.schema.json',
        'src/adaos/abi/builder.semantic_prototype_candidate.v2.schema.json',
        'src/adaos/abi/ui.capability_catalog.v1.json', 'src/adaos/services/builder/semantic_prototype.py',
        'src/adaos/services/builder/semantic_owner_references.py', 'src/adaos/e2e/semantic_coverage.py',
        'src/adaos/services/builder/semantic_input.py', 'src/adaos/e2e/semantic_input_review.py',
        'src/adaos/services/builder/generation_plan.py', 'src/adaos/services/builder/provider_jobs.py',
        'src/adaos/services/builder/task_pipeline.py', 'src/adaos/services/builder/repair_context.py',
        'src/adaos/services/builder/semantic_presentations.py', 'src/adaos/services/builder/semantic_media.py',
        'src/adaos/services/builder/semantic_sections.py', 'src/adaos/services/builder/semantic_surfaces.py',
        'src/adaos/services/webui_contract.py',
        'src/adaos/services/builder/prototype_mcp.py', 'src/adaos/services/ui_capabilities.py',
        'src/adaos/services/builder/domain_packs/application_manager_legacy.py',
        str(inventory_path.relative_to(root)).replace('\\', '/')]
    return {'schema': 'adaos.evidence.semantic_coverage.v1',
        'ok': all(row['lowering_accepted'] for row in cases), 'model_calls': 0,
        'semantic_equivalence_proven': False, 'equal_abi_expressiveness_proven': False,
        'llm_understanding_proven': False, 'witnesses': cases, 'widgets': rows,
        'actions': [{'id': i, 'witnesses': observed['action'].get(i, [])} for i in sorted(runtime_actions)],
        'data_sources': [{'id': i, 'witnesses': observed['data_source'].get(i, [])} for i in sorted(runtime_sources)],
        'client_inventory_freshness': inventory_freshness(inventory_path.parents[2], inventory),
        'client_registration_scope': 'checked_in_inventory_not_live_browser_registration',
        'semantic_boundaries': {'view_roles': semantic['$defs']['view']['properties']['role']['enum'],
            'condition_operators': semantic['$defs']['condition']['properties']['operator']['enum'],
            'capacity': {k: v['maxItems'] for k, v in semantic['properties'].items() if 'maxItems' in v},
            'abi_top_level_without_same_named_semantic_property': sorted(set(abi['properties']) - set(semantic['properties'])),
            'absence_policy': 'Missing named properties and witnesses require review; neither proves behavioral inexpressibility.'},
        'catalog_coverage': catalog['coverage'],
        'enforced_owner_rules': owner_reference_contract(),
        'model_input_boundary': {'scope': 'prospective_provider_schema_and_canonical_inline_retrieval_fragments_only',
            'actual_paid_wire_observed': False, 'machine_owner_rules_in_guidance': 'reference_ownership' in guidance,
            'existing_owner_prose_fragments': ['coverage', 'command_ownership', 'modeling'],
            'understanding': 'Provider admission and fragment equality do not establish model understanding.'},
        'recorded_input': recorded_input,
        'provider_schema_digest': _hash(provider), 'guidance_digest': _hash(guidance),
        'summary': {'abi_widget_types': len(widget_types), 'inventory_widget_types': len(runtime_widgets),
            'widget_lowering_status': dict(Counter(row['semantic_lowering'] for row in rows)),
            'witnesses_passed': sum(row['lowering_accepted'] for row in cases), 'witnesses_total': len(cases)},
        'source_pins': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in source_paths}}


def execute(inputs, context, *, repo_root):
    from adaos.e2e.builder import _write_json
    output = Path(context['bundle_dir']) / 'semantic-coverage'
    output.mkdir(exist_ok=False)
    request_ref = inputs.get('recorded_request_ref') or os.getenv('ADAOS_E2E_SEMANTIC_COVERAGE_REQUEST_REF')
    report = audit(repo_root, recorded_request_ref=request_ref)
    profile = inputs.get('semantic_input_profile') or os.getenv('ADAOS_E2E_SEMANTIC_INPUT_PROFILE')
    if profile:
        if not request_ref:
            raise ValueError('An isolated input review needs a retained generation request')
        from adaos.e2e.semantic_input_review import review_saved_generation, message_objects
        import re
        root = Path(repo_root).resolve()
        request_path = (root / request_ref).resolve()  # audit already enforced retained-evidence containment.
        record = _read(request_path)
        tasks = [body['builder_request'] for body in message_objects(record['messages']) if 'builder_request' in body]
        ref = tasks[0].get('generation_plan_ref') if len(tasks) == 1 else None
        if not isinstance(ref, str) or not re.fullmatch(r'generation-plan:[a-f0-9]{24}', ref):
            raise ValueError('Retained request needs one exact generation plan identity')
        plan_path = (request_path.parent / (ref.replace(':', '-') + '.json')).resolve()
        if not plan_path.is_relative_to((root / 'e2e/artifacts/builder').resolve()):
            raise ValueError('Recorded plan must be retained Builder evidence')
        report['isolated_input_baseline'], packet = review_saved_generation(record, _read(plan_path), profile=profile)
        _write_json(output / 'isolated-input-review.json', packet)
    _write_json(output / 'report.json', report)
    table = ['# Semantic coverage: structural witnesses, not behavioral equivalence', '',
        '| Widget | ABI | Client inventory | Catalog | Semantic lowering |', '| --- | --- | --- | --- | --- |']
    table += [f"| {r['id']} | {r['abi_declared']} | {bool(r['client_registration'])} | {r['catalog_profile']} | {r['semantic_lowering']} |" for r in report['widgets']]
    table += ['', '## Named construction witnesses', '',
        '| Construction | Provider schema | Compiler | Browser behavior |', '| --- | --- | --- | --- |']
    table += [f"| {r['id']} | {r['provider_schema_accepted']} | {r['lowering_accepted']} | {r['runtime_behavior']} |" for r in report['witnesses']]
    table += ['', 'A missing witness is unknown, not an unsupported capability. A passing witness does not cover all renderer inputs or compositions.']
    (output / 'map.md').write_text('\n'.join(table) + '\n', encoding='utf-8')
    return report
