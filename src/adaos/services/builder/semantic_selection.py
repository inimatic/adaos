"""Resolve semantic master/detail links without model-authored runtime state ids."""

from .workflow import BuilderWorkflowError


def selection_findings(document):
    views = {view['id']: view for view in document['views']}
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
        else:
            linked = any(
                (relationship['cardinality'] in {'many_to_one', 'one_to_one'}
                 and (relationship['from_resource_ref'], relationship['from_field_ref'], relationship['to_resource_ref'], relationship['to_field_ref'])
                 == (view['resource_ref'], link['field_ref'], source['resource_ref'], 'id'))
                or (relationship['cardinality'] == 'one_to_many'
                    and (relationship['to_resource_ref'], relationship['to_field_ref'], relationship['from_resource_ref'], relationship['from_field_ref'])
                    == (view['resource_ref'], link['field_ref'], source['resource_ref'], 'id'))
                for relationship in document['relationships']
            )
            if not linked:
                detail = 'selection_filter needs a declared foreign-key relationship to the source record id'
        if detail:
            findings.append({'code': 'semantic.selection_filter_invalid', 'path': f'$.views[{index}].selection_filter',
                             'semantic_refs': [f"view:{view['id']}"], 'detail': detail})
    return findings


def compile_selection_filters(document, webui, source_map):
    widgets = {widget['id']: widget for widget in webui['ui']['application']['desktop']['pageSchema']['widgets']}
    for view in document['views']:
        link = view.get('selection_filter')
        if not link:
            continue
        source = widgets[link['source_view_ref']]
        selection = next((key for action in source.get('actions', [])
                          if action.get('on') == 'select' and action.get('type') == 'updateState'
                          for key, expression in action.get('params', {}).items() if expression == '$event.id'), None)
        if not selection:
            raise BuilderWorkflowError('selection_filter source has no compiled record-selection action')
        widgets[view['id']]['dataSource']['query'].setdefault('filters', {})[link['field_ref']] = f'$state.{selection}'
        path = f"ui.application.desktop.pageSchema.widgets.@{view['id']}.dataSource.query.filters.{link['field_ref']}"
        source_map.setdefault(f"field:{link['field_ref']}", []).append(path)
        source_map.setdefault(f"view:{view['id']}", []).append(path)
