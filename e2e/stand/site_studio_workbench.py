"""Compose Site Studio's workbench while retaining its admitted command contracts.

The connected candidate is never installed by the review seeder. Review mode
replaces every skill call with local fixtures, and exercises CRUD on copies.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json


def update(on, **params):
    return {'on': on, 'type': 'updateState', 'params': params}


def open_dialog(on, identifier, **state):
    return {'on': on, 'type': 'openModal', 'params': {'modalId': identifier, 'statePatch': state}}


def toolbar(identifier, buttons, actions, **inputs):
    return {'id': identifier, 'type': 'ui.actions', 'area': 'authoring',
            'inputs': {'variant': 'adaptiveToolbar', 'buttons': buttons, **inputs}, 'actions': actions}


def icon(identifier, label, name):
    return {'id': identifier, 'label': label, 'title': label, 'icon': name, 'showLabel': False}


def modal(title, widgets):
    for widget in widgets:
        widget['area'] = 'main'
        widget.pop('visibleIf', None)
    return {'title': title, 'scope': 'workspace', 'presentation': {'kind': 'modal'},
            'schema': {'id': 'studio-' + widgets[0]['id'], 'layout': {'version': 2, 'pattern': 'workbench',
                       'contentWidth': 'fluid', 'scroll': 'page', 'regions': [
                           {'id': 'main', 'role': 'main', 'scroll': 'page',
                            'size': {'grow': 1}, 'presentation': {'wide': 'pane', 'compact': 'stack'}}]},
                       'widgets': widgets}}


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def command_contracts(webui):
    """Include nested followups, CAS, payloads, confirmations and invalidations."""
    return Counter(json.dumps(node, sort_keys=True, ensure_ascii=False)
                   for node in walk(webui) if node.get('type') == 'callSkill')


def compose(legacy):
    result = deepcopy(legacy)
    app = result['ui']['application']
    page = app['desktop']['pageSchema']
    old = {w['id']: w for w in page['widgets']}
    page['layout'] = {'version': 2, 'pattern': 'workbench', 'density': 'compact',
        'contentWidth': 'fluid', 'scroll': 'regions', 'regions': [
            {'id': 'authoring', 'label': 'Редактор', 'role': 'navigation', 'scroll': 'region',
             'size': {'minPx': 330, 'preferredPx': 420, 'maxPx': 900, 'grow': 0, 'resizable': True, 'remember': True},
             'presentation': {'wide': 'pane', 'compact': 'drawer'}},
            {'id': 'preview', 'label': 'Предпросмотр', 'role': 'main', 'scroll': 'region',
             'size': {'minPx': 460, 'grow': 1}, 'presentation': {'wide': 'pane', 'compact': 'stack'}}]}
    state = page.setdefault('initialState', {})
    state.update(authoring_tool='chat', structure_view='sections', selected_source_anchor='', selected_section_label='Весь сайт')
    widgets = []
    widgets.append(toolbar('site_identity', [
        {'id': 'site-name', 'label': '$state.site_project_name'},
        {'id': 'version', 'label': '$state.publication_context.item.active_revision_id',
         'title': 'Текущая редакция и публикации'},
        icon('open-projects', 'Открыть или создать сайт', 'folder-open-outline'),
        icon('knowledge', 'О проекте', 'information-circle-outline')], [
            open_dialog('click:site-name', 'site_knowledge'),
            update('click:version', authoring_tool='publication'),
            open_dialog('click:open-projects', 'site_projects'),
            open_dialog('click:knowledge', 'site_knowledge')], singleLine=True))
    tabs = [('chat', 'Чат'), ('structure', 'Структура'), ('materials', 'Материалы'),
            ('changes', 'Изменения'), ('publication', 'Публикация'), ('theme', 'Тема')]
    widgets.append(toolbar('authoring_tools', [{'id': k, 'label': v} for k, v in tabs], [
        update('click:' + k, authoring_tool=k, inspector_mode={'theme': 'theme', 'requests': 'requests'}.get(k, 'content'),
               authoring_tab='publication' if k == 'publication' else 'discussion' if k == 'chat' else 'canvas')
        for k, _ in tabs], variant='tabsWithMore', selectedStateKey='authoring_tool'))
    widgets.append(toolbar('chat_context', [
        {'id': 'context', 'label': 'Весь сайт', 'title': 'Контекст сообщения',
         'optionsDataSource': deepcopy(old['v_sections']['dataSource']), 'optionValuePath': 'id',
         'optionLabelPath': 'heading', 'selectedStateKey': 'selected_sections_id',
         'fixedOptions': [{'id': '', 'label': 'Весь сайт'}]},
        icon('properties', 'Свойства', 'options-outline')], [
            update('select:context', selected_sections_id='$event.id', selected_section_label='$event.label',
                   discussion_subject={'kind': 'expression', 'op': 'if',
                       'condition': {'kind': 'expression', 'op': 'equals', 'args': ['$event.id', '']},
                       'then': 'site', 'else': 'block'}),
            {'on': 'click:properties', 'type': 'openModal', 'params': {'modalId': {
                'kind': 'expression', 'op': 'if',
                'condition': {'kind': 'expression', 'op': 'equals', 'args': ['$state.selected_sections_id', '']},
                'then': 'site_knowledge', 'else': 'section_properties'}}}], singleLine=True))
    widgets[-1]['visibleIf'] = "$state.authoring_tool === 'chat'"
    widgets.append(toolbar('structure_tools', [{'id': k, 'label': label} for k, label in [
        ('sections', 'Разделы'), ('catalog', 'Добавить'), ('objects', 'Объекты')]], [
        update('click:' + k, structure_view=k) for k in ['sections', 'catalog', 'objects']],
        variant='tabs', selectedStateKey='structure_view'))
    widgets[-1]['visibleIf'] = "$state.authoring_tool === 'structure'"
    # Retain every existing widget and its commands; only its placement changes.
    moved = {
        'site_knowledge': ('О проекте', ['target_description', 'description_proposals']),
        'section_properties': ('Свойства', ['e_section', 'd_section']),
        'asset_properties': ('Материал сайта: изображение или видео', ['e_assets']),
        'material_intake_dialog': ('Справочный текст для работы над сайтом', ['material_intake']),
        'block_discussion': ('Обсудить раздел', ['block_remark', 'block_feedback']),
    }
    request_ids = [identifier for identifier in ['queries-v_requests', 'v_requests', 'd_request'] if identifier in old]
    if request_ids:
        moved['legacy_requests'] = ('Прежние задачи по сайту', request_ids)
    relocated = {identifier for _, ids in moved.values() for identifier in ids}
    for name, (title, ids) in moved.items():
        app.setdefault('modals', {})[name] = modal(title, [old[identifier] for identifier in ids])
    about = app['modals']['site_knowledge']['schema']['widgets']
    old['description_proposals']['inputs'].pop('detailsKey', None)
    old['description_proposals']['inputs']['detailsPath'] = 'comparison'
    about.append(toolbar('discuss_target', [{'id': 'discuss-target', 'label': 'Обсудить цель в чате'}], [
        {'on': 'click:discuss-target', 'type': 'closeModal', 'followups': [
            {'type': 'updateState', 'params': {'authoring_tool': 'chat', 'discussion_subject': 'target'}}]}]))
    about[-1]['area'] = 'main'
    if request_ids:
        requests = app['modals']['legacy_requests']['schema']['widgets']
        requests.insert(0, {'id': 'legacy_requests_explanation', 'type': 'ui.form', 'area': 'main',
            'inputs': {'fields': [{'id': 'explanation', 'type': 'staticContent', 'content':
                'Это записи прежнего сценария о подготовке материалов и правках сайта. '
                'Текущую работу обсуждаем в чате, результат проверяем в «Изменениях». '
                'Обращения посетителей из форм сайта здесь пока не собираются.'}]}})
        widgets.append(toolbar('legacy_requests_entry', [{'id': 'open', 'label': 'Прежние задачи по сайту'}],
                               [open_dialog('click:open', 'legacy_requests')]))
        widgets[-1]['visibleIf'] = "$state.authoring_tool === 'changes'"
    for identifier, widget in old.items():
        if identifier in relocated:
            continue
        widget['area'] = 'authoring'
        if identifier == 'site_preview':
            widget.update(area='preview', sizing='fill')
            widget.pop('visibleIf', None)
            widget['inputs'].update(showInteractionControls=True, interactionMode='$state.canvas_mode',
                interactionModeStateKey='canvas_mode', selectedSourceAnchor='$state.selected_source_anchor',
                selectedSection='$state.selected_sections_id',
                inspectorModeStateKey='selected_object_view')
        elif identifier == 'v_sections':
            widget['visibleIf'] = "$state.authoring_tool === 'structure' && $state.structure_view === 'sections'"
            widget['inputs']['titleKey'] = 'heading'
            widget['inputs']['buttons'] = [icon('properties', 'Свойства', 'options-outline'),
                                            icon('discuss', 'Обсудить', 'ticket-outline')]
            widget['actions'].extend([open_dialog('click:properties', 'section_properties', selected_sections_id='$event.id'),
                                      open_dialog('click:discuss', 'block_discussion', selected_sections_id='$event.id'),
                                      update('select', selected_section_label='$event.heading', discussion_subject='block')])
        elif identifier == 'site_discussion':
            widget.update(sizing='fill', visibleIf="$state.authoring_tool === 'chat'")
        elif identifier.startswith('publication_') or identifier.startswith('component_upgrade'):
            widget['visibleIf'] = "$state.authoring_tool === 'publication'"
        elif identifier in ['queries-v_assets', 'v_assets', 'site_materials']:
            widget['visibleIf'] = "$state.authoring_tool === 'materials'"
            if identifier == 'v_assets':
                widget.update(title='Материалы сайта')
                widget.pop('title_i18n', None)
                widget['inputs']['headerActions'] = [{'id': 'add-asset', 'label': 'Добавить материал', 'icon': 'add-outline'}]
                widget['actions'].extend([open_dialog('select', 'asset_properties'),
                    open_dialog('click:add-asset', 'asset_properties', selected_assets_id='')])
            if identifier == 'site_materials':
                widget.update(title='Источники и справочные материалы')
                widget.pop('title_i18n', None)
                widget['inputs']['headerActions'] = [{'id': 'intake', 'label': 'Добавить справочный текст'}]
                widget.setdefault('actions', []).append(open_dialog('click:intake', 'material_intake_dialog'))
        elif identifier in ['v_theme', 'e_theme']:
            widget['visibleIf'] = "$state.authoring_tool === 'theme'"
        elif identifier in ['queries-v_requests', 'v_requests', 'd_request']:
            widget['visibleIf'] = "$state.authoring_tool === 'requests'"
        elif identifier in ['change_proposals', 'revision_history', 'site_diagnostics', 'semantic_source', 'source_inspector']:
            widget['visibleIf'] = "$state.authoring_tool === 'changes'"
        elif identifier in ['site_structure', 'component_catalog']:
            widget['visibleIf'] = "$state.authoring_tool === 'structure' && $state.structure_view === '" + ('objects' if identifier == 'site_structure' else 'catalog') + "'"
        else:
            # Redundant old navigation survives in the candidate for contract
            # parity; its operations remain available through the new controls.
            widget['visibleIf'] = 'false'
        widgets.append(widget)
    page['widgets'] = widgets
    missing = command_contracts(legacy) - command_contracts(result)
    if missing:
        raise ValueError('Workbench dropped or changed retained skill commands')
    result['generated_by'] = 'site_studio_workbench_prototype'
    return result


def local_crud(widget, collection, selected):
    widget['dataSource'] = {'kind': 'static', 'value': '$state.' + collection}
    fields = widget['inputs']['fields']
    for field in fields:
        field.pop('stateKey', None)
        if field.get('type') == 'fileUpload':
            field.update(type='staticContent', content='Загрузка файла доступна в действующем Site Studio. Здесь проверяем диалог на локальной копии.')
            for key in ['uploadTarget', 'readTarget', 'fileStorage']:
                field.pop(key, None)
    for act in widget['actions']:
        operation = act.get('params', {}).get('operation')
        if operation not in ['create', 'update', 'delete']:
            continue
        operations = []
        if operation in ['update', 'delete']:
            operations.append({'op': 'toggleArrayItem', 'path': collection, 'itemKey': 'id', 'value': '$event.record'})
        if operation in ['create', 'update']:
            fields_payload = {field['id']: '$event.values.' + field['id'] for field in fields if field['type'] != 'staticContent'}
            fields_payload.update(id='$state.' + selected if operation == 'update' else 'review-asset-$state.review_sequence', revision=1)
            operations.extend([{'op': 'increment', 'path': 'review_sequence'},
                               {'op': 'toggleArrayItem', 'path': collection, 'itemKey': 'id', 'value': fields_payload}])
        act.update(type='mutateState', params={'operations': operations}, followups=[{'type': 'closeModal'}])
        for key in ['target', 'resultStateKey', 'invalidates']:
            act.pop(key, None)


def review(connected, fixture, snapshots):
    result = deepcopy(connected)
    snapshots = dict(snapshots)
    history = snapshots.get('query_site_history', {})
    version_model = review_version(snapshots['query_publication']['item'], history)
    # History receipts can include full compiled previews. The list needs only
    # display fields and retained action guards, never duplicate page bundles.
    history_fields = {'id', 'title', 'created_at', 'result_digest', 'summary', 'status_label',
                      'validation_status', 'origin_label', 'revision_id', 'undo_available', 'undo_reason',
                      'change_set_id', 'expected_source_digest'}
    snapshots['query_site_history'] = {key: [{k: v for k, v in row.items() if k in history_fields}
        for row in history.get(key, [])[:20]] for key in ['revisions', 'change_sets']}
    app = result['ui']['application']; page = app['desktop']['pageSchema']
    fp = fixture['ui']['application']['desktop']['pageSchema']
    fw = {w['id']: w for w in fp['widgets']}
    state = page['initialState']; state.update(deepcopy(fp['initialState']))
    state.update(project_hydrated=True, site_project_id='adaos-site', site_project_site_id='adaos',
                 site_project_name='AdaOS', project_selection_revision=6, review_sequence=1,
                 selected_assets_id='', selected_theme_id='default', selected_object_view='',
                 review_assets=deepcopy(snapshots['query_assets']['items']),
                 publication_context=deepcopy(snapshots['query_publication']))
    state['publication_context']['item'].update(can_prepare=False, can_publish=False)
    state['publication_context']['item']['availability_text'] = 'Прототип: публикация и доменные операции не запускаются. Действующая редакция сохранена.'
    snapshots['query_publication'] = deepcopy(state['publication_context'])
    page.pop('autoActions', None)
    # No skill query, upload or mutation may escape the isolated UI review.
    for node in list(walk(result)):
        if node.get('kind') == 'skill':
            name = node.get('name', '').rsplit('.', 1)[-1]
            value = deepcopy(snapshots.get(name, {'items': []}))
            if name == 'query_sections': value = '$state.review_sections'
            if name == 'query_assets': value = '$state.review_assets'
            if name == 'query_preview': value = '$state.preview_bundle'
            node.clear(); node.update(kind='static', value=value)
        if node.get('type') == 'callSkill':
            identity = {key: node[key] for key in ['id', 'on', 'enabledIf', 'confirmation'] if key in node}
            if not node.get('target', '').rsplit('.', 1)[-1].startswith('query_'):
                identity['enabledIf'] = 'false'
            node.clear(); node.update(type='none', **identity)
        if node.get('type') == 'fileUpload':
            node.update(type='staticContent', content='Загрузка файла выполняется в действующем редакторе.')
            for key in ['uploadTarget', 'readTarget', 'fileStorage']: node.pop(key, None)
    state['review_sections'] = deepcopy(snapshots['query_sections']['items'])
    anchors = {r['id']: r['source_anchor'] for r in fw['site_structure']['dataSource']['value']}
    for row in state['review_sections']:
        row['source_anchor'] = anchors.get(row['id'], '')
    hero = next(r for r in state['review_sections'] if r['id'] == 'hero')
    state['selected_section_label'] = hero['heading']
    widgets = {w['id']: w for w in page['widgets']}
    state['review_version'] = version_model
    identity = widgets['site_identity']
    next(button for button in identity['inputs']['buttons'] if button['id'] == 'version').update(label='$state.review_version.header')
    identity['actions'] = [open_dialog('click:version', 'site_version') if action['on'] == 'click:version' else action
                           for action in identity['actions']]
    version_details = {'id': 'site_version_details', 'type': 'item.details', 'area': 'main',
        'dataSource': {'kind': 'static', 'value': {'item': version_model}}, 'inputs': {'fields': [
            {'id': key, 'label': label} for key, label in [
                ('notice', 'Нумерация'), ('published', 'Опубликованная версия'), ('current', 'Текущий черновик'),
                ('pending', 'После публикации'), ('release_rule', 'Новый релиз'), ('domains', 'Домены')]]}}
    version_changes = toolbar('version_changes', [{'id': 'changes', 'label': 'Показать правки'}], [
        {'on': 'click:changes', 'type': 'closeModal', 'followups': [
            {'type': 'updateState', 'params': {'authoring_tool': 'changes'}}]}])
    app['modals']['site_version'] = modal('Версия сайта', [version_details, version_changes])
    context = widgets['chat_context']
    context['inputs']['buttons'][0].update(options=[{'id': '', 'label': 'Весь сайт', 'source_anchor': ''}] + [
        {'id': r['id'], 'label': r['heading'], 'source_anchor': r['source_anchor']} for r in state['review_sections']])
    context['inputs']['buttons'][0].pop('optionsDataSource', None)
    context['actions'][0]['params']['selected_source_anchor'] = '$event.source_anchor'
    for widget in page['widgets']:
        if widget['id'] == 'site_discussion':
            widget.clear(); widget.update(deepcopy(fw['site_discussion']))
        if widget['id'] == 'site_preview':
            widget['inputs']['minHeight'] = 480
        if widget['id'] == 'v_sections':
            widget['actions'].append(update('select', selected_source_anchor='$event.source_anchor'))
    # Restore the real material fields and their local create/update/delete
    # semantics after removing all live callSkill commands from the fixture.
    asset = deepcopy(connected['ui']['application']['modals']['asset_properties']['schema']['widgets'][0])
    local_crud(asset, 'review_assets', 'selected_assets_id')
    app['modals']['asset_properties']['schema']['widgets'][0] = asset
    # Record-bound forms keep the existing revision/draft behaviour in modals.
    section = app['modals']['section_properties']['schema']['widgets'][0]
    section['dataSource'] = {'kind': 'static', 'value': '$state.review_sections'}
    section['inputs']['buttons'] = [{'id': 'local-save', 'label': 'Сохранить в прототипе', 'kind': 'primary'}]
    section['actions'] = [{'id': 'local-save', 'on': 'submit', 'type': 'closeModal'}]
    app['modals']['section_properties']['schema']['widgets'] = property_copy(
        state['preview_bundle']['source'], state)
    # Site picker uses the same dialog and retains create/select controls. Its
    # review copy never changes the shared active project of the live service.
    state['review_projects'] = [{'id': 'adaos-site', 'title': 'AdaOS', 'active': True}]
    for widget in app['modals']['site_projects']['schema']['widgets']:
        if widget['id'] == 'local_site_projects':
            widget['dataSource'] = {'kind': 'static', 'value': '$state.review_projects'}
            widget['actions'] = [update('select', site_project_id='$event.id', site_project_name='$event.title'), {'on': 'select', 'type': 'closeModal'}]
        if widget['id'] == 'create_local_site':
            widget['actions'] = [{'id': 'create-local', 'on': 'submit', 'type': 'mutateState', 'params': {'operations': [
                {'op': 'increment', 'path': 'review_sequence'},
                {'op': 'toggleArrayItem', 'path': 'review_projects', 'value': {'id': 'review-site-$state.review_sequence', 'title': '$event.values.name', 'disabled': True}}]},
                'followups': [{'type': 'closeModal'}]}, {'id': 'cancel', 'on': 'submit', 'type': 'closeModal'}]
    # Preserve the compiled comparison demo without pretending to publish it.
    for identifier in ['change_diff', 'change_review']:
        page['widgets'].append(deepcopy(fw[identifier]))
    page.setdefault('meta', {})['builder'] = {'authored_webui': True, 'prototype_only': True}
    return result


def review_version(publication, history):
    """Illustrate release numbers from a verified ancestry, never persist them.

    The 0.1.0 baseline is a proposal, not an inferred production release. Count
    actual parent links, not numeric gaps in revision IDs. Unknown ancestry is
    explicit; undo and failed operations must not rewind or invent versions.
    """
    active = publication.get('active_revision_id', '')
    published = publication.get('published_revision_id', '')
    revisions = {r['revision_id']: r for r in history.get('revisions', [])}
    current = active
    seen = set()
    while current and current != published and current not in seen:
        seen.add(current)
        current = revisions.get(current, {}).get('parent_revision_id', '')
    count = len(seen) if published and current == published else None
    pending = publication.get('changes_pending', True)
    version = f'0.1.{count}' if count is not None else None
    return {
        'header': (f'v{version}' if version else active or 'Черновик') + (' · черновик' if pending else ' · опубликовано'),
        'notice': 'Пример нумерации для обсуждения: опубликованной редакции условно присвоена 0.1.0. '
                  'Действующие релизы не перенумерованы.',
        'published': f'0.1.0 · {published}' if published else 'Сайт ещё не опубликован',
        'current': f'{version} · {active}' if version else active,
        'pending': f'Сохранённых редакций: {count}' if count is not None else 'История от опубликованной редакции недоступна',
        'release_rule': 'Сохранённая правка получает следующий patch-номер. При публикации можно выбрать minor или major. '
                        'Набор текста без сохранения версию не меняет.',
        'domains': 'Один сайт, несколько адресов. Каждый адрес указывает на выбранный релиз; '
                   'проверенную версию можно перенести с beta-домена на основной без пересборки.',
        'pending_count': count,
    }


def property_copy(source, state):
    """Bounded, typed review of objects/collections without a raw JSON editor.

    This is a UI fixture, not a component schema or write authority. Connecting
    nested writes requires owned field schemas and stable collection item IDs.
    """
    state.update(property_tab='block', selected_property_node='')
    widgets = [toolbar('property_tabs', [{'id': 'block', 'label': 'Параметры блока'},
                                        {'id': 'items', 'label': 'Элементы'}],
        [update('click:block', property_tab='block'), update('click:items', property_tab='items')],
        variant='tabs', selectedStateKey='property_tab')]
    widgets[0]['area'] = 'main'
    resources = {r['id']: r['data'] for r in source['resources']}
    titles = {'title': 'Заголовок', 'body': 'Текст', 'label': 'Название', 'description': 'Описание',
              'name': 'Имя', 'href': 'Ссылка', 'statement': 'Основной текст', 'accent': 'Акцент',
              'columns': 'Колонки', 'tone': 'Тон', 'variant': 'Вариант', 'headingHidden': 'Скрыть заголовок'}
    for section in source['routes'][0]['sections']:
        identifier = section['id']; data = resources[section['resource_ref']]
        forms = []; count = [0]
        def visit(value, path, label):
            if count[0] >= 500 or len(path.split('.')) > 10:
                return None
            count[0] += 1
            node_id = identifier + ':' + path
            children = []
            if isinstance(value, dict):
                scalar = {k: v for k, v in value.items() if v is None or isinstance(v, (str, int, float, bool))}
                if scalar:
                    fields = []
                    for k, v in scalar.items():
                        key = f'component_drafts.{identifier}.{path}.{k}'
                        state[key] = v
                        fields.append({'id': k, 'title': titles.get(k, k),
                            'type': 'toggle' if isinstance(v, bool) else 'number' if isinstance(v, (int, float)) else 'longText' if isinstance(v, str) and len(v) > 150 else 'shortText',
                            'stateKey': key})
                    forms.append({'id': 'property-' + str(len(widgets) + len(forms)), 'type': 'ui.form', 'area': 'main',
                        'title': label, 'visibleIf': f"$state.selected_sections_id === '{identifier}' && " +
                            ("$state.property_tab === 'block'" if path == 'block' else f"$state.property_tab === 'items' && $state.selected_property_node === '{node_id}'"),
                        'inputs': {'autoCommit': True, 'fields': fields, 'buttons': [{'id': 'done', 'label': 'Готово'}]},
                        'actions': [{'id': 'done', 'on': 'submit', 'type': 'closeModal'}]})
                for k, v in value.items():
                    if isinstance(v, (dict, list)):
                        child = visit(v, path + '.' + k, titles.get(k, k))
                        if child: children.append(child)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    title = str(item.get('title') or item.get('name') or item.get('label') or f'Элемент {i+1}') if isinstance(item, dict) else str(item)
                    child = visit(item if isinstance(item, (dict, list)) else {'value': item}, path + '.' + str(i), title)
                    if child: children.append(child)
            return {'id': node_id, 'title': label, 'children': children}
        root = visit(data, 'block', section.get('label') or data.get('title') or identifier)
        widgets.append({'id': 'property-tree-' + identifier, 'type': 'collection.tree', 'area': 'main',
            'title': 'Элементы блока',
            'visibleIf': f"$state.selected_sections_id === '{identifier}' && $state.property_tab === 'items'",
            'dataSource': {'kind': 'static', 'value': root['children']},
            'inputs': {'selectedStateKey': 'selected_property_node', 'expanded': True, 'hideRoot': True, 'maxNodes': 500},
            'actions': [update('select', selected_property_node='$event.id')]})
        widgets.extend(forms)
    return widgets
