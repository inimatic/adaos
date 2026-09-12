"""Compile explicit content partitions using existing navigation/modal contracts."""

import copy

from .workflow import BuilderWorkflowError


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
    for identifier, section in sections.items():
        label, label_i18n = localize(section['title'], dictionaries)
        button = {'id': identifier, 'label': label, 'label_i18n': label_i18n}
        members = [widget for widget in widgets if owned.get(widget['id']) == identifier]
        if not members:
            raise BuilderWorkflowError(f'Section {identifier} has no reachable content')
        if section['kind'] == 'tab':
            tabs.append(button)
            condition = f"$state.prototype_section === '{identifier}'"
            for widget in members:
                previous = widget.get('visibleIf')
                widget['visibleIf'] = f'({previous}) && ({condition})' if previous else condition
        else:
            modal_id = f'settings-{identifier}'
            if modal_id in application.get('modals', {}):
                raise BuilderWorkflowError(f'Settings modal collision: {modal_id}')
            settings.append({**button, 'icon': 'settings-outline', 'modal_id': modal_id})
            areas = [area for area in page['layout']['areas'] if any(widget['area'] == area['id'] for widget in members)]
            application.setdefault('modals', {})[modal_id] = {
                'title': label, 'title_i18n': label_i18n,
                'schema': {'id': modal_id, 'layout': {**copy.deepcopy(page['layout']), 'areas': areas}, 'widgets': members},
            }
            for widget in members:
                widgets.remove(widget)
                old = f"ui.application.desktop.pageSchema.widgets.@{widget['id']}"
                new = f"ui.application.modals.{modal_id}.schema.widgets.@{widget['id']}"
                for refs in source_map.values():
                    refs[:] = [ref.replace(old, new, 1) if ref == old or ref.startswith(old + '.') else ref for ref in refs]
    if settings:
        widgets.insert(0, {'id': 'prototype-settings', 'type': 'ui.actions', 'area': 'primary',
                          'inputs': {'variant': 'adaptiveToolbar', 'buttons': [{key: value for key, value in item.items() if key != 'modal_id'} for item in settings]},
                          'actions': [{'on': f"click:{item['id']}", 'type': 'openModal', 'params': {'modalId': item['modal_id']}} for item in settings]})
    if tabs:
        page['initialState']['prototype_section'] = tabs[0]['id']
        widgets.insert(0, {'id': 'prototype-sections', 'type': 'ui.actions', 'area': 'primary',
                          'inputs': {'variant': 'tabs', 'selectedStateKey': 'prototype_section', 'buttons': tabs},
                          'actions': [{'on': f"click:{item['id']}", 'type': 'updateState', 'params': {'prototype_section': item['id']}} for item in tabs]})
