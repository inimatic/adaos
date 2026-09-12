"""Permanent collection predicates, separate from resettable query controls."""

import copy


def scope_findings(document, *, valid_value):
    resources = {resource['id']: resource for resource in document['resources']}
    findings = []
    for index, view in enumerate(document['views']):
        scopes = view.get('scope_filters') or []
        if not scopes:
            continue
        fields = {field['id']: field for field in resources[view['resource_ref']]['fields']}
        refs = [item['field_ref'] for item in scopes]
        dynamic = {control.get('field_ref') for control in view.get('query_controls') or []}
        dynamic.add((view.get('filter') or {}).get('field_ref'))
        detail = None
        if view['role'] != 'collection':
            detail = 'scope_filters belong to collection views only'
        elif len(refs) != len(set(refs)) or set(refs).intersection(dynamic):
            detail = 'scope_filters must be unique and cannot overlap resettable controls or selection filters'
        elif any(item['field_ref'] not in fields
                 or fields[item['field_ref']]['value_type'] not in {'short_text', 'choice', 'number', 'boolean', 'date'}
                 or item['value'] == '' or not valid_value(fields[item['field_ref']], item['value']) for item in scopes):
            detail = 'scope_filters require nonempty, correctly typed scalar field values'
        if detail:
            findings.append({'code': 'semantic.scope_filter_invalid', 'path': f'$.views[{index}].scope_filters',
                             'semantic_refs': [f"view:{view['id']}"], 'detail': detail})
    return findings


def scoped_predicates(state, view):
    return [*copy.deepcopy(state.get('filters') or []),
            *[{'field_ref': item['field_ref'], 'operator': 'eq', 'value': copy.deepcopy(item['value'])}
              for item in view.get('scope_filters') or []]]


def legacy_state(state, views):
    result = {key: copy.deepcopy(value) for key, value in state.items() if key != 'proof'}
    if state.get('proof', {}).get('kind') != 'collection_empty':
        result['filters'] = scoped_predicates(state, views[state['view_ref']])
    return result


def compile_query_scopes(document, webui, source_map):
    widgets = {widget['id']: widget for widget in webui['ui']['application']['desktop']['pageSchema']['widgets']}
    for view in document['views']:
        for item in view.get('scope_filters') or []:
            widgets[view['id']]['dataSource']['query'].setdefault('filters', {})[item['field_ref']] = copy.deepcopy(item['value'])
            source_map.setdefault(f"field:{item['field_ref']}", []).append(
                f"ui.application.desktop.pageSchema.widgets.@{view['id']}.dataSource.query.filters.{item['field_ref']}")
