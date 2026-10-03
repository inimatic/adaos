"""Compile explicit content partitions using existing navigation/modal contracts."""

import copy

from .workflow import BuilderWorkflowError


def _navigation_area(page, member_areas):
    regions = page.get('layout', {}).get('regions') or []
    region_by_id = {str(region.get('id')): region for region in regions}
    used = list(dict.fromkeys(str(area) for area in member_areas if area))
    declared_used = [area for area in used if area in region_by_id]
    if len(declared_used) == 1:
        return declared_used[0]
    for role in ('toolbar', 'main', 'collection', 'inspector', 'navigation', 'utility'):
        match = next(
            (
                area
                for area in declared_used
                if str(region_by_id[area].get('role')) == role
            ),
            None,
        )
        if match:
            return match
    if declared_used:
        return declared_used[0]
    if 'primary' in region_by_id or not region_by_id:
        return 'primary'
    for role in ('toolbar', 'main', 'collection', 'inspector', 'navigation', 'utility'):
        match = next(
            (str(region['id']) for region in regions if str(region.get('role')) == role),
            None,
        )
        if match:
            return match
    return str(regions[0]['id'])


def compile_sections(document, webui, source_map, dictionaries, *, localize):
    application = webui['ui']['application']
    page = application['desktop']['pageSchema']
    widgets = page['widgets']
    by_id = {widget['id']: widget for widget in widgets}
    sections = {}
    owned = {}
    for view in document['views']:
        section = view.get('section')
        if not section:
            continue
        identifier = section['id']
        previous = sections.setdefault(identifier, section)
        if previous != section:
            raise BuilderWorkflowError(f'Conflicting title or kind for section {identifier}')
        for widget_id in (view['id'], f"queries-{view['id']}", f"open-{view['id']}"):
            if widget_id in by_id:
                owned[widget_id] = identifier
    if not sections:
        return
    reserved = {'prototype-sections', 'prototype-settings'}
    if reserved.intersection(by_id):
        raise BuilderWorkflowError('Authored view id collides with section navigation')
    tabs = []
    settings = []
    tab_member_areas = []
    settings_member_areas = []
    for identifier, section in sections.items():
        label, label_i18n = localize(section['title'], dictionaries)
        button = {'id': identifier, 'label': label, 'label_i18n': label_i18n}
        members = [widget for widget in widgets if owned.get(widget['id']) == identifier]
        if not members:
            raise BuilderWorkflowError(f'Section {identifier} has no reachable content')
        if section['kind'] == 'tab':
            tabs.append(button)
            tab_member_areas.extend(widget['area'] for widget in members)
            condition = f"$state.prototype_section === '{identifier}'"
            for widget in members:
                previous = widget.get('visibleIf')
                widget['visibleIf'] = f'({previous}) && ({condition})' if previous else condition
        else:
            modal_id = f'settings-{identifier}'
            if modal_id in application.get('modals', {}):
                raise BuilderWorkflowError(f'Settings modal collision: {modal_id}')
            settings.append({**button, 'icon': 'settings-outline', 'modal_id': modal_id})
            settings_member_areas.extend(widget['area'] for widget in members)
            regions = [copy.deepcopy(region) for region in page['layout']['regions'] if any(widget['area'] == region['id'] for widget in members)]
            for index, region in enumerate(regions):
                if index == 0:
                    region['role'] = 'main'
                    region['priority'] = 100
                    region['scroll'] = 'page'
                    region['presentation'] = {'wide': 'pane', 'compact': 'stack'}
                elif region['role'] in {'collection', 'main', 'detail', 'inspector'}:
                    region['role'] = 'utility'
            modal_layout = {key: copy.deepcopy(value) for key, value in page['layout'].items() if key != 'variants'}
            modal_layout['pattern'] = 'settings'
            modal_layout['contentWidth'] = 'reading'
            modal_layout['scroll'] = 'page'
            modal_layout['regions'] = regions
            application.setdefault('modals', {})[modal_id] = {
                'title': label, 'title_i18n': label_i18n,
                'schema': {'id': modal_id, 'layout': modal_layout, 'widgets': members},
            }
            for widget in members:
                widgets.remove(widget)
                old = f"ui.application.desktop.pageSchema.widgets.@{widget['id']}"
                new = f"ui.application.modals.{modal_id}.schema.widgets.@{widget['id']}"
                for refs in source_map.values():
                    refs[:] = [ref.replace(old, new, 1) if ref == old or ref.startswith(old + '.') else ref for ref in refs]
    if settings:
        widgets.insert(0, {'id': 'prototype-settings', 'type': 'ui.actions', 'area': _navigation_area(page, settings_member_areas),
                          'inputs': {'variant': 'adaptiveToolbar', 'buttons': [{key: value for key, value in item.items() if key != 'modal_id'} for item in settings]},
                          'actions': [{'on': f"click:{item['id']}", 'type': 'openModal', 'params': {'modalId': item['modal_id']}} for item in settings]})
    if tabs:
        page['initialState']['prototype_section'] = tabs[0]['id']
        widgets.insert(0, {'id': 'prototype-sections', 'type': 'ui.actions', 'area': _navigation_area(page, tab_member_areas),
                          'inputs': {'variant': 'tabs', 'selectedStateKey': 'prototype_section', 'buttons': tabs},
                          'actions': [{'on': f"click:{item['id']}", 'type': 'updateState', 'params': {'prototype_section': item['id']}} for item in tabs]})
