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
    detail_sources = {}
    for view in views.values():
        source_ref = view.get('activation_source_view_ref')
        if source_ref:
            detail_sources.setdefault(source_ref, []).append(view['id'])
    graph = {}
    for view in views.values():
        if link := view.get('selection_filter'):
            graph.setdefault(link['source_view_ref'], set()).add(view['id'])
    findings = []
    for index, view in enumerate(document['views']):
        policy = view.get('selection')
        if view['role'] != 'collection' and policy is not None:
            findings.append({
                'code': 'semantic.selection_policy_invalid',
                'path': f'$.views[{index}].selection',
                'semantic_refs': [f"view:{view['id']}"],
                'detail': 'selection policy belongs to collection views only',
            })
        elif policy and (
            (policy.get('mode') == 'none' and policy.get('row_activation') != 'none')
            or (
                policy.get('mode') == 'single'
                and policy.get('row_activation') not in {'select', 'open_details'}
            )
            or (policy.get('indicator') == 'radio' and policy.get('mode') != 'single')
        ):
            findings.append({
                'code': 'semantic.selection_policy_invalid',
                'path': f'$.views[{index}].selection',
                'semantic_refs': [f"view:{view['id']}"],
                'detail': 'selection mode, indicator and row_activation are inconsistent',
            })
        if policy and policy.get('row_activation') == 'open_details' and not detail_sources.get(view['id']):
            findings.append({
                'code': 'semantic.detail_activation_missing',
                'path': f'$.views[{index}].selection.row_activation',
                'semantic_refs': [f"view:{view['id']}"],
                'detail': 'row_activation=open_details requires a details view linked by activation_source_view_ref',
            })
        activation_source_ref = view.get('activation_source_view_ref')
        if activation_source_ref is not None:
            source = views.get(activation_source_ref)
            detail = None
            if view['role'] != 'details':
                detail = 'activation_source_view_ref belongs to details views only'
            elif not source or source.get('role') != 'collection':
                detail = 'activation_source_view_ref must reference a collection view'
            elif source.get('resource_ref') != view.get('resource_ref'):
                detail = 'activated collection and details must project the same resource'
            elif (source.get('selection') or {}).get('row_activation') != 'open_details':
                detail = 'activation source collection must declare row_activation=open_details'
            if detail:
                findings.append({
                    'code': 'semantic.detail_activation_invalid',
                    'path': f'$.views[{index}].activation_source_view_ref',
                    'semantic_refs': [f"view:{view['id']}"],
                    'detail': detail,
                })
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
        elif source.get('presentation') == 'chart' or (source.get('selection') or {}).get('mode') == 'none':
            detail = 'selection_filter source must support record selection; charts do not'
        elif link.get('effect', 'filter') == 'emphasize' and view.get('presentation') != 'table':
            detail = 'selection_filter effect=emphasize currently requires a table target'
        elif link.get('effect', 'filter') == 'filter' and link['field_ref'] in other_filters:
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
        widget = widgets[view['id']]
        policy = view.get('selection') or {
            'mode': 'single', 'indicator': 'row_accent', 'row_activation': 'select',
        }
        if policy.get('mode') == 'none' or policy.get('row_activation') == 'none':
            widget['actions'] = [
                action for action in widget.get('actions', [])
                if action.get('on') != 'select'
            ]
            widget.setdefault('inputs', {}).update(
                selectionMode='none', selectionIndicator='none', rowActivation='none'
            )
            continue
        for action in widget.get('actions', []):
            if action.get('on') != 'select' or action.get('type') != 'updateState':
                continue
            for key, expression in action.get('params', {}).items():
                if expression == '$event.id':
                    selections[view['id']] = key
                    actions_by_selection.setdefault(key, []).append(action)
                    widget.setdefault('inputs', {}).update(
                        selectedStateKey=key,
                        selectionMode=policy.get('mode', 'single'),
                        selectionIndicator=policy.get('indicator', 'row_accent'),
                        rowActivation=(
                            'open-detail'
                            if policy.get('row_activation') == 'open_details'
                            else policy.get('row_activation', 'select')
                        ),
                    )
                    source_map.setdefault(f"view:{view['id']}", []).append(
                        f"ui.application.desktop.pageSchema.widgets.@{view['id']}.inputs.selectedStateKey"
                    )

    detail_activation_state_key = '__adaos_active_detail_source'
    for view in document['views']:
        source_ref = view.get('activation_source_view_ref')
        if view.get('role') != 'details' or not source_ref:
            continue
        widget = widgets[view['id']]
        widget.setdefault('inputs', {})['activationSourceWidgetId'] = source_ref
        widget['visibleIf'] = (
            f'$state.{detail_activation_state_key} === "{source_ref}"'
        )
        page.setdefault('initialState', {}).setdefault(detail_activation_state_key, '')
        source_map.setdefault(f"view:{view['id']}", []).extend([
            f"ui.application.desktop.pageSchema.widgets.@{view['id']}.inputs.activationSourceWidgetId",
            f"ui.application.desktop.pageSchema.widgets.@{view['id']}.visibleIf",
        ])
        source_map.setdefault(f"view:{source_ref}", []).append(
            f"ui.application.desktop.pageSchema.widgets.@{view['id']}.inputs.activationSourceWidgetId"
        )
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
        target_widget = widgets[view['id']]
        if link.get('effect', 'filter') == 'emphasize':
            target_widget.setdefault('inputs', {})['relationshipEmphasis'] = {
                'stateKey': value_key,
                'fieldKey': link['field_ref'],
                'emptySelection': link.get('empty_selection', 'show_all'),
            }
            path = f"ui.application.desktop.pageSchema.widgets.@{view['id']}.inputs.relationshipEmphasis.fieldKey"
        else:
            target_widget['dataSource']['query'].setdefault('filters', {})[link['field_ref']] = f'$state.{value_key}'
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
