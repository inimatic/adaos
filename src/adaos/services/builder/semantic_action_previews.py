"""Non-mutating reviews of deferred integrations in disposable Prototypes.

Core owns the disclosure and the navigation-only lowering. A model cannot turn
these controls into external calls or claim a successful operational outcome.
"""
from __future__ import annotations

from .workflow import BuilderWorkflowError


def validate_action_previews(document):
    obligations = {row['requirement_ref'] for row in document.get('automation_requirements', [])
                   if row.get('reason') == 'external_integration'}
    for view in document['views']:
        actions = view.get('preview_actions') or []
        if not actions:
            continue
        if view['role'] != 'collection' or view.get('presentation') not in {'list', 'table', 'cards'}:
            raise BuilderWorkflowError('Action previews require a list, table or cards collection')
        if len({row['id'] for row in actions}) != len(actions):
            raise BuilderWorkflowError('Action preview ids must be unique within their collection')
        if sum(row['primary'] for row in actions) > 2:
            raise BuilderWorkflowError('Action previews allow at most two primary controls')
        if any(row['requirement_ref'] not in obligations for row in actions):
            raise BuilderWorkflowError('Action previews require an exact external integration obligation')


def compile_action_previews(document, webui, source_map, dictionaries, *, localize):
    application = webui['ui']['application']
    page = application['desktop']['pageSchema']
    widgets = {row['id']: row for row in page['widgets']}
    notice, notice_i18n = localize({
        'key': 'prototype.action_preview.notice',
        'en': 'Prototype preview. No operation was executed. The external integration is deferred to Automation.',
        'ru': 'Демонстрация. Операция не выполнена. Подключение внешнего действия предусмотрено при Automation.',
    }, dictionaries)
    more, more_i18n = localize({'key': 'prototype.action_preview.more', 'en': 'More', 'ru': 'Ещё'}, dictionaries)
    for view in document['views']:
        actions = view.get('preview_actions') or []
        if not actions:
            continue
        widget = widgets[view['id']]
        buttons = widget.setdefault('inputs', {}).setdefault('buttons', [])
        events = widget.setdefault('actions', [])
        secondary = []

        def modal(identifier, title, title_i18n, content):
            if identifier in application.get('modals', {}):
                raise BuilderWorkflowError('Action preview modal identity collision')
            application.setdefault('modals', {})[identifier] = {
                'title': title, 'title_i18n': title_i18n,
                'schema': {'id': identifier, 'layout': {
                    'version': 2, 'pattern': 'task-flow', 'density': 'comfortable',
                    'contentWidth': 'reading', 'scroll': 'page',
                    'regions': [{'id': 'main', 'role': 'main', 'priority': 100,
                                 'scroll': 'page', 'presentation': {'wide': 'pane', 'compact': 'stack'}}],
                }, 'widgets': content},
            }

        for action in actions:
            identifier = f"preview-{view['id']}-{action['id']}"
            if any(row.get('id') == identifier for row in buttons):
                raise BuilderWorkflowError('Action preview button identity collision')
            label, label_i18n = localize(action['label'], dictionaries)
            detail, detail_i18n = localize(action['description'], dictionaries)
            modal(identifier, label, label_i18n, [{
                'id': identifier + '-review', 'type': 'item.details', 'area': 'main',
                'dataSource': {'kind': 'static', 'value': {'id': 'preview'}},
                'inputs': {'presentation': 'section', 'fields': [
                    {'id': 'description', 'label': '', 'value': detail, 'value_i18n': detail_i18n},
                    {'id': 'notice', 'label': '', 'value': notice, 'value_i18n': notice_i18n},
                ]},
            }])
            button = {'id': identifier, 'label': label, 'label_i18n': label_i18n}
            event = {'on': f'click:{identifier}', 'type': 'navigateModal', 'params': {'modalId': identifier}}
            if action['primary']:
                buttons.append(button)
                events.append(event)
            else:
                secondary.append((button, event))
            source_map.setdefault(f"view:{view['id']}", []).append(
                f'ui.application.modals.{identifier}.schema.widgets.@{identifier}-review')
        if secondary:
            identifier = f"preview-{view['id']}-more"
            buttons.append({'id': identifier, 'label': more, 'label_i18n': more_i18n, 'icon': 'ellipsis-horizontal'})
            events.append({'on': f'click:{identifier}', 'type': 'navigateModal', 'params': {'modalId': identifier}})
            modal(identifier, more, more_i18n, [{
                'id': identifier + '-actions', 'type': 'ui.actions', 'area': 'main',
                'inputs': {'buttons': [row[0] for row in secondary]},
                'actions': [row[1] for row in secondary],
            }])
        page['meta']['builder']['external_action_previews'] = True
