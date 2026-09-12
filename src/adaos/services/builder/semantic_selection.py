"""Resolve semantic master/detail links without model-authored runtime state ids."""

from .workflow import BuilderWorkflowError


def _reachable(graph, start):
    found, pending = set(), list(graph.get(start, ()))
    while pending:
        node = pending.pop()
        if node not in found:
            found.add(node)
            pending.extend(graph.get(node, ()))
    return found


def selection_findings(document):
    views = {view['id']: view for view in document['views']}
    graph = {}
    for view in views.values():
        if link := view.get('selection_filter'):
            graph.setdefault(link['source_view_ref'], set()).add(view['id'])
    findings = []
    for index, view in enumerate(document['views']):
        link = view.get('selection_filter')
        if not link:
            continue
        source = views.get(link['source_view_ref'])
        other_filters = {item.get('field_ref') for item in view.get('query_controls') or []}
        other_filters.update(item['field_ref'] for item in view.get('scope_filters') or [])
        other_filters.add((view.get('filter') or {}).get('field_ref'))
        detail = None
        if view['role'] != 'collection' or not source or source['role'] != 'collection' or source['id'] == view['id']:
            detail = 'selection_filter requires distinct source and target collections'
        elif source.get('presentation') == 'chart':
            detail = 'selection_filter source must support record selection; charts do not'
        elif link['field_ref'] in other_filters:
            detail = 'selection_filter cannot overlap resettable, permanent or legacy filters'
        elif view['id'] in _reachable(graph, view['id']):
            detail = 'selection_filter links must not form a cycle'
        else:
            endpoints = ((source['resource_ref'], link.get('source_field_ref') or 'id'),
                         (view['resource_ref'], link['field_ref']))
            linked = False
            for relationship in document['relationships']:
                pair = ((relationship['from_resource_ref'], relationship['from_field_ref']),
                        (relationship['to_resource_ref'], relationship['to_field_ref']))
                singular = relationship['cardinality'] in {'many_to_one', 'one_to_one', 'one_to_many'}
                if singular and endpoints in (pair, pair[::-1]):
                    linked = True
                    break
            if not linked:
                detail = 'selection_filter needs a declared singular relationship between the exact source and target fields'
        if detail:
            findings.append({'code': 'semantic.selection_filter_invalid', 'path': f'$.views[{index}].selection_filter',
                             'semantic_refs': [f"view:{view['id']}"], 'detail': detail})
    return findings


def compile_selection_filters(document, webui, source_map):
    page = webui['ui']['application']['desktop']['pageSchema']
    widgets = {widget['id']: widget for widget in page['widgets']}
    selections, actions_by_selection = {}, {}
    for view in document['views']:
        if view['role'] != 'collection':
            continue
        for action in widgets[view['id']].get('actions', []):
            if action.get('on') != 'select' or action.get('type') != 'updateState':
                continue
            for key, expression in action.get('params', {}).items():
                if expression == '$event.id':
                    selections[view['id']] = key
                    actions_by_selection.setdefault(key, []).append(action)
    graph, reverse, derived = {}, {}, {}
    for view in document['views']:
        link = view.get('selection_filter')
        if not link:
            continue
        source_id = link['source_view_ref']
        selection = selections.get(source_id)
        if not selection:
            raise BuilderWorkflowError('selection_filter source has no compiled record-selection action')
        graph.setdefault(source_id, set()).add(view['id'])
        reverse.setdefault(view['id'], set()).add(source_id)
        field = link.get('source_field_ref') or 'id'
        value_key = selection
        if field != 'id':
            pair = (selection, field)
            if pair not in derived:
                value_key = f'{selection}__linked_value_{len(derived)}'
                while value_key in page['initialState']:
                    value_key += '_'
                derived[pair] = value_key
                page['initialState'][value_key] = ''
                for action in actions_by_selection[selection]:
                    action['params'][value_key] = f'$event.{field}'
            value_key = derived[pair]
        widgets[view['id']]['dataSource']['query'].setdefault('filters', {})[link['field_ref']] = f'$state.{value_key}'
        path = f"ui.application.desktop.pageSchema.widgets.@{view['id']}.dataSource.query.filters.{link['field_ref']}"
        source_map.setdefault(f"field:{link['field_ref']}", []).append(path)
        source_map.setdefault(f"view:{view['id']}", []).append(path)
        source_map.setdefault(f'field:{field}', []).append(path)

    # Selection is resource-scoped: all representations must reset the same descendants,
    # while a child-to-parent lookup must never clear its ancestor's selection.
    for view_id, selected_key in selections.items():
        sources = {view_id for view_id, key in selections.items() if key == selected_key}
        descendants = set().union(*(_reachable(graph, source) for source in sources))
        ancestors = {view_id} | _reachable(reverse, view_id)
        protected = {selections.get(view_id) for view_id in ancestors}
        cleared = {selections[view_id] for view_id in descendants if view_id in selections} - protected
        cleared.update(value for (selection, _), value in derived.items() if selection in cleared)
        for action in widgets[view_id].get('actions', []):
            if action.get('on') == 'select' and action.get('type') == 'updateState':
                action['params'].update({key: '' for key in sorted(cleared)})
