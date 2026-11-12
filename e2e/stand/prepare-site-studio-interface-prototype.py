"""Prepare an isolated, authored-WebUI Site Studio review without Automation.

Uses an exported owned preview and shared widgets. Chat responses and release
controls are labelled prototype fixtures; no LLM or publication is invoked.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
SCENARIO = 'site_studio_workbench_20261111'


def button(identifier, label, icon=None, **extra):
    return {'id': identifier, 'label': label, **({'icon': icon} if icon else {}), **extra}


def action(on, **state):
    return {'on': on, 'type': 'updateState', 'params': state}


def commands(identifier, buttons, actions, *, visible=None, title=None, variant='autoOverflow', **inputs):
    return {'id': identifier, 'type': 'ui.actions', 'area': 'authoring',
            **({'title': title} if title else {}), **({'visibleIf': visible} if visible else {}),
            'inputs': {'variant': variant, 'buttons': buttons, **inputs}, 'actions': actions}


def details(identifier, title, values, *, visible, fields=None):
    return {'id': identifier, 'type': 'item.details', 'area': 'authoring', 'title': title,
            'visibleIf': visible, 'dataSource': {'kind': 'static', 'value': values},
            'inputs': {'fields': fields or [{'key': key, 'label': key} for key in values]}}


def build(preview, candidate):
    source = preview['source']
    resources = {item['id']: item for item in source['resources']}
    entries = preview['source_map']['entries']
    rows = []
    editors = []
    drafts = {}
    for section in source['routes'][0]['sections']:
        data = resources[section['resource_ref']]['data']
        brand = data.get('brand')
        title = str(data.get('title') or (brand.get('name') if isinstance(brand, dict) else brand) or section['id'])
        anchor = next(e['route_anchor'] for e in entries if e['widget_id'] == section['id'])
        rows.append({'id': section['id'], 'label': title, 'component': section['component'],
                     'source_anchor': anchor, 'description': section['component'].removeprefix('site.')})
        record = {'heading': data.get('title', data.get('brand', '') if isinstance(data.get('brand'), str) else title),
                  'body': data.get('body') or '\n\n'.join(data.get('paragraphs', [])),
                  'heading_hidden': bool(data.get('headingHidden', False))}
        drafts[section['id']] = record
        fields = [{'id': 'heading', 'type': 'shortText', 'title': 'Заголовок',
                   'stateKey': f"drafts.{section['id']}.heading"},
                  {'id': 'body', 'type': 'longText', 'title': 'Текст',
                   'stateKey': f"drafts.{section['id']}.body"},
                  {'id': 'heading_hidden', 'type': 'toggle', 'title': 'Скрыть заголовок',
                   'stateKey': f"drafts.{section['id']}.heading_hidden"}]
        editors.append({'id': 'edit-' + section['id'], 'type': 'ui.form', 'area': 'authoring',
                        'title': title, 'visibleIf': f"$state.authoring_tool === 'data' && $state.selected_sections_id === '{section['id']}'",
                        'inputs': {'layout': 'responsiveGrid', 'autoCommit': True, 'fields': fields,
                                   'buttons': [button('save', 'Сохранить черновик', kind='primary'),
                                               button('delete', 'Удалить раздел', kind='danger', enabledIf='$state.prototype_delete_enabled === true')]},
                        'actions': [{'id': 'save', **action('click:save', prototype_form_saved=True)},
                                    {'id': 'delete', **action('click:delete', authoring_tool='changes')}]})
    hero = next((row for row in rows if row['component'] == 'site.hero'), rows[0])
    chat = [{'id': 'intro', 'from': 'assistant', 'text':
             'Работаем над AdaOS для beta.adaos.io. Выберите раздел в структуре или прямо в preview — он станет контекстом запроса.\n\n'
             'Это прототип интерфейса: ответы ИИ и публикация пока демонстрационные. Данные рабочего сайта не изменяются.'}]
    for seq, (phase, label, text) in enumerate([
        ('inspect', 'Контекст', 'Пример: выбран главный экран, прочитаны текст и ограничения компонента.'),
        ('validate', 'Проверка', 'Пример: подготовлен локальный вариант текста. Остальные разделы сохранены.'),
        ('review', 'Результат', 'Демонстрационный вариант готов к просмотру. Откройте «Изменения», сравните и примените его только в этом прототипе.'),
    ]):
        chat.append({'id': 'example-' + phase, 'from': 'assistant', 'text': text,
                     'progress_group_id': 'prototype-example', 'progress_phase': phase,
                     'progress_label': label, 'progress_seq': seq, 'progress_status': 'completed'})
    widgets = [
        commands('site_identity', [button('site', 'AdaOS · beta.adaos.io', 'globe-outline'),
                                  button('knowledge', 'О проекте', 'information-circle-outline')],
                 [action('click:site', authoring_tool='sites'), action('click:knowledge', authoring_tool='knowledge')],
                 title='Site Studio · прототип'),
        commands('authoring_tools', [button(k, v) for k, v in [('chat', 'Чат'), ('structure', 'Структура'),
                 ('materials', 'Материалы'), ('changes', 'Изменения'), ('publication', 'Публикация')]],
                 [action('click:' + k, authoring_tool=k) for k in ['chat', 'structure', 'materials', 'changes', 'publication']],
                 variant='tabsWithMore', selectedStateKey='authoring_tool'),
        commands('chat_context', [button('site', 'Весь сайт', 'globe-outline'),
                                  button(hero['id'], 'Главный экран', 'locate-outline'),
                                  button('properties', 'Свойства', 'options-outline')],
                 [action('click:site', selected_sections_id='', selected_source_anchor=''),
                  action('click:' + hero['id'], selected_sections_id=hero['id'], selected_source_anchor=hero['source_anchor']),
                  action('click:properties', authoring_tool='data')],
                 visible="$state.authoring_tool === 'chat'", selectedStateKey='selected_sections_id'),
        commands('composer_intent', [button('discuss', 'Обсудить'), button('edit', 'Изменить сайт')],
                 [action('click:discuss', composer_intent='discuss'), action('click:edit', composer_intent='edit')],
                 visible="$state.authoring_tool === 'chat'", variant='tabs', selectedStateKey='composer_intent'),
        {'id': 'site_discussion', 'type': 'ui.chat', 'area': 'authoring', 'sizing': 'fill',
         'visibleIf': "$state.authoring_tool === 'chat'", 'dataSource': {'kind': 'static', 'value': {'messages': '$state.chat_messages'}},
         'inputs': {'fillHeight': True, 'multiline': True, 'composerRows': 3,
                    'placeholder': 'Что изменить? Можно выбрать элемент справа…',
                    'conversation': {'version': 1, 'owner': 'site-studio:interface-review', 'label': 'AdaOS · прототип',
                                     'agent': {'mode': 'fixed', 'id': 'site-studio-review', 'label': 'Site Studio'},
                                     'history': {'mode': 'manual', 'maxMessages': 50}, 'voice': False,
                                     'presentation': {'floating': False}}},
         'actions': [action('send', chat_messages=chat + [
             {'id': 'prototype-user', 'from': 'user', 'text': '$event.text'},
             {'id': 'prototype-reply', 'from': 'assistant', 'text':
              'Запрос отображён в прототипе. Реальный запуск ИИ подключим после согласования интерфейса. Пример изменения доступен во вкладке «Изменения».'}])]},
        {'id': 'site_structure', 'type': 'ui.list', 'area': 'authoring', 'title': 'Разделы страницы',
         'visibleIf': "$state.authoring_tool === 'structure'", 'dataSource': {'kind': 'static', 'value': rows},
         'inputs': {'itemIdKey': 'id', 'titleKey': 'label', 'selectedStateKey': 'selected_sections_id',
                    'selectionMode': 'single', 'variant': 'list',
                    'headerActions': [button('add-component', 'Добавить раздел', 'add-outline')],
                    'meta': [{'key': 'description', 'label': 'Компонент', 'kind': 'text'}],
                    'buttons': [button('properties', 'Свойства', 'create-outline'), button('chat', 'Обсудить', 'chatbubble-outline')]},
         'actions': [action('click:add-component', authoring_tool='catalog'),
                     action('select', selected_sections_id='$event.id', selected_source_anchor='$event.source_anchor'),
                     action('click:properties', selected_sections_id='$event.id', selected_source_anchor='$event.source_anchor', authoring_tool='data'),
                     action('click:chat', selected_sections_id='$event.id', selected_source_anchor='$event.source_anchor', authoring_tool='chat')]},
        commands('selected_actions', [button('chat', 'Обсудить выбранное', 'chatbubble-outline'),
                                      button('back', 'К структуре', 'arrow-back-outline')],
                 [action('click:chat', authoring_tool='chat'), action('click:back', authoring_tool='structure')],
                 visible="$state.authoring_tool === 'data'"),
        *editors,
        details('local_save', 'Черновик формы', {'Статус': 'Локальное состояние прототипа. Сохранение рабочего сайта подключим после ревью.'},
                visible="$state.authoring_tool === 'data' && $state.prototype_form_saved === true"),
        details('no_selection', 'Выберите раздел', {'Подсказка': 'Откройте структуру или включите «Выбор» в шапке preview и нажмите на раздел.'},
                visible="$state.authoring_tool === 'data' && $state.selected_sections_id === ''"),
        details('site_knowledge', 'О проекте', {'Сайт': 'AdaOS', 'Адрес для экспериментов': 'beta.adaos.io',
                'Задача': 'Объяснить платформу, сценарии использования и путь начала работы.',
                'Ограничения': 'Редактируем готовые компоненты. Рабочую редакцию фонда сохраняем.',
                'Контекст': 'Принятые факты проекта будут храниться отдельно от истории чата.'},
                visible="$state.authoring_tool === 'knowledge'"),
        details('site_picker', 'Сайт', {'Текущий проект': 'AdaOS · Beta',
                'Режим': 'Изолированный прототип. Другие сайты здесь не изменяются.'}, visible="$state.authoring_tool === 'sites'"),
        {'id': 'component_catalog', 'type': 'ui.list', 'area': 'authoring', 'title': 'Добавить раздел · готовые компоненты',
         'visibleIf': "$state.authoring_tool === 'catalog'", 'dataSource': {'kind': 'static', 'value': [
             {'id': identifier, 'label': label, 'note': 'В прототипе показан выбор типа; добавление в рабочий черновик подключим после ревью.'}
             for identifier, label in [('site.section', 'Текстовый раздел'), ('site.media', 'Изображение или видео'),
                 ('site.cardGrid', 'Карточки'), ('site.flow', 'Этапы'), ('site.dashboard', 'Дашборд'), ('site.map', 'Карта'),
                 ('site.divider', 'Разделитель'), ('site.callToAction', 'Призыв к действию')]]},
         'inputs': {'itemIdKey': 'id', 'titleKey': 'label', 'variant': 'list',
                    'meta': [{'key': 'note', 'label': 'Статус', 'kind': 'text', 'overflow': 'wrap'}]}},
        {'id': 'site_materials', 'type': 'ui.list', 'area': 'authoring', 'title': 'Материалы и медиа',
         'visibleIf': "$state.authoring_tool === 'materials'", 'dataSource': {'kind': 'static', 'value': [
             {'id': asset['id'], 'label': asset['alt'], 'kind': asset['media_type'], 'rights': asset.get('rights', '')}
             for asset in source['assets']]},
         'inputs': {'itemIdKey': 'id', 'titleKey': 'label', 'variant': 'list',
                    'meta': [{'key': 'kind', 'label': 'Тип', 'kind': 'text'}, {'key': 'rights', 'label': 'Права', 'kind': 'text'}]}},
        details('change_diff', 'Пример изменения · главный экран', {
            'До': resources[next(s['resource_ref'] for s in source['routes'][0]['sections'] if s['id'] == hero['id'])]['data'].get('statement', ''),
            'После': 'Your workspace. Your software. Your rules.',
            'Область': 'Только текст главного экрана', 'Проверка': 'Демонстрационный вариант скомпилирован для preview'},
            visible="$state.authoring_tool === 'changes'"),
        commands('change_review', [button('before', 'До'), button('after', 'После'),
                                  button('apply', 'Применить', 'checkmark-outline'), button('undo', 'Отменить', 'arrow-undo-outline')],
                 [action('click:before', preview_candidate=False), action('click:after', preview_candidate=True),
                  action('click:apply', preview_candidate=True, candidate_applied=True),
                  action('click:undo', preview_candidate=False, candidate_applied=False)],
                 visible="$state.authoring_tool === 'changes'"),
        details('publication_status', 'Публикация AdaOS · Beta', {
            'Адрес': 'https://beta.adaos.io/', 'Статус': 'Подготовка платформенного домена',
            'Черновик': source['revision']['id'], 'Публичная редакция': 'В прототипе не изменяется',
            'Доставка': 'Global Root → Cloudflare; RU Root → REG.RU',
            'Индексирование': 'Проверка HTML, robots.txt и sitemap.xml запланирована',
            'Важно': 'Действия этой панели демонстрационные; публикация не запускается'},
            visible="$state.authoring_tool === 'publication'"),
        commands('publication_actions', [button('prepare', 'Подготовить публикацию', 'cloud-upload-outline', enabledIf='$state.publication_enabled === true')], [],
                 visible="$state.authoring_tool === 'publication'"),
        {'id': 'site_preview', 'type': 'visual.sitePreview', 'area': 'preview', 'sizing': 'fill', 'title': 'AdaOS · предпросмотр',
         'inputs': {'siteId': source['id'], 'route': '/', 'minHeight': 480, 'rememberViewport': True,
                    'showInteractionControls': True, 'interactionMode': '$state.canvas_mode',
                    'interactionModeStateKey': 'canvas_mode', 'selectedSourceAnchor': '$state.selected_source_anchor',
                    'inspectorModeStateKey': 'authoring_tool'},
         'dataSource': {'kind': 'static', 'value': '$state.preview_bundle'}},
    ]
    # Both candidates use a persistent preview. State changes select a bundle;
    # unrelated tool/draft changes must not remount or reapply it.
    widgets[-1]['dataSource']['value'] = '$state.preview_bundle'
    for widget in widgets:
        if widget['id'] == 'change_review':
            for a in widget['actions']:
                a['params']['preview_bundle'] = candidate if a['params']['preview_candidate'] else preview
    return {'schema': 'adaos.webui.v1', 'generated_by': 'site_studio_workbench_prototype',
            'ui': {'version': 1, 'application': {'desktop': {'pageSchema': {
                'id': SCENARIO, 'title': 'Site Studio · новый интерфейс',
                'layout': {'version': 2, 'pattern': 'workbench', 'density': 'compact', 'contentWidth': 'fluid',
                           'scroll': 'regions', 'regions': [
                               {'id': 'authoring', 'label': 'Редактор', 'role': 'navigation', 'priority': 90, 'scroll': 'region',
                                'size': {'minPx': 330, 'preferredPx': 420, 'maxPx': 600, 'grow': 0},
                                'presentation': {'wide': 'pane', 'compact': 'drawer'}},
                               {'id': 'preview', 'label': 'Предпросмотр', 'role': 'main', 'priority': 100, 'scroll': 'region',
                                'size': {'minPx': 460, 'grow': 1}, 'presentation': {'wide': 'pane', 'compact': 'stack'}}],
                           'interaction': {'selection': 'none', 'rowActivation': 'none', 'filters': 'disclosure', 'actions': 'adaptive'}},
                'initialState': {'authoring_tool': 'chat', 'composer_intent': 'discuss', 'selected_sections_id': hero['id'],
                                 'selected_source_anchor': hero['source_anchor'], 'canvas_mode': 'navigate',
                                 'preview_bundle': preview, 'chat_messages': chat, 'drafts': drafts,
                                 'prototype_form_saved': False, 'candidate_applied': False},
                'widgets': widgets,
                'meta': {'builder': {'authored_webui': True, 'prototype_only': True,
                                     'source': 'owner-approved Site Studio plan; stop for UI review before Automation'}}}},
            'resources': {}, 'modals': {}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('preview', type=Path)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--legacy-webui', type=Path, help='Exported admitted Site Studio WebUI; retain its command contracts')
    parser.add_argument('--snapshots', type=Path, help='Directory of read-only query snapshots for isolated review')
    parser.add_argument('--output', type=Path, default=ROOT / 'e2e/artifacts/site-studio-interface-20261111')
    args = parser.parse_args()
    preview = json.loads(args.preview.read_text(encoding='utf-8'))
    assert preview['schema'] == 'adaos.site_preview.v1' and preview['source']['id'] == 'adaos'
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.services.site_studio.compiler import compile_site_revision
    from adaos.services.site_studio.contracts import digest
    import requests
    session = requests.Session(); session.trust_env = False
    session.headers['X-AdaOS-Token'] = resolve_control_token(base_url='http://127.0.0.1:8777')
    assets = {}
    for asset in preview['source']['assets']:
        ref = preview['preview_assets'][asset['public_path']]
        if not ref.startswith('/api/tools/') or '/read_attachment/' not in ref:
            raise ValueError('Expected an owner-bound preview attachment')
        response = session.get('http://127.0.0.1:8777' + ref, timeout=35); response.raise_for_status()
        assert digest(response.content) == asset['digest']
        assets[asset['digest']] = response.content
    source = copy.deepcopy(preview['source'])
    hero = next(s for s in source['routes'][0]['sections'] if s['component'] == 'site.hero')
    resource = next(r for r in source['resources'] if r['id'] == hero['resource_ref'])
    resource['data']['statement'] = 'Your workspace. Your software. Your rules.'
    source['revision']['id'] = 'prototype-candidate-001'
    candidate = compile_site_revision(source, asset_resolver=lambda a: assets[a['digest']])['preview']
    candidate['artifact_page_digest'] = digest(candidate['page'])
    def transport(value):
        if isinstance(value, str):
            return preview['preview_assets'].get(value, value)
        if isinstance(value, list):
            return [transport(item) for item in value]
        if isinstance(value, dict):
            return {key: transport(item) for key, item in value.items()}
        return value
    candidate['page'] = transport(candidate['page'])
    candidate['page_digest'] = digest(candidate['page'])
    candidate.update(preview_transport='authenticated_blob_urls', preview_assets=preview['preview_assets'])
    webui = build(preview, candidate)
    connected = None
    if args.legacy_webui:
        if not args.snapshots:
            parser.error('--snapshots is required with --legacy-webui')
        from site_studio_workbench import compose, review, command_contracts
        legacy = json.loads(args.legacy_webui.read_text(encoding='utf-8-sig'))
        connected = compose(legacy)
        snapshots = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in args.snapshots.glob('query_*.json')}
        webui = review(connected, webui, snapshots)
    from adaos.sdk.developer import ui
    validation = ui.validate_contract(webui)
    if not validation['ok']:
        print(json.dumps(validation, ensure_ascii=False, indent=2)); raise SystemExit(1)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'prototype.webui.json').write_text(json.dumps(webui, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if connected:
        connected_validation = ui.validate_contract(connected)
        if not connected_validation['ok']:
            print(json.dumps(connected_validation, ensure_ascii=False)); raise SystemExit(1)
        (args.output / 'workbench.connected.webui.json').write_text(json.dumps(connected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (args.output / 'compatibility.json').write_text(json.dumps({
            'retained_skill_commands': sum(command_contracts(legacy).values()),
            'missing_skill_commands': sum((command_contracts(legacy) - command_contracts(connected)).values()),
            'connected_candidate_installed': False, 'review_only': True,
        }, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'validated': True, 'widgets': len(webui['ui']['application']['desktop']['pageSchema']['widgets']),
                      'source_revision': preview['source']['revision']['id'], 'output': str(args.output)}, ensure_ascii=False))
    if args.install:
        import logging
        logging.disable(logging.CRITICAL)
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        from adaos.apps.cli.app import Settings, init_ctx
        init_ctx(Settings.from_sources())
        from adaos.sdk.developer import projects, compositions
        from adaos.sdk.builder import workflow
        root = projects.resolve_root('scenario', SCENARIO, required=False)
        if not root.exists():
            compositions.create_with_primary_component(SCENARIO, kind='scenario', template='scenario_default',
                title='Site Studio · новый интерфейс [Prototype]', description='Owner review; AdaOS only; no Automation or publication',
                tags=['prototype', 'site-studio', 'owner-review'],
                entrypoints=[{'id': 'main', 'presentation': 'scenario:' + SCENARIO, 'default': True, 'bindings': {}}])
        else:
            existing = json.loads((root / 'webui.json').read_text(encoding='utf-8-sig'))
            assert existing.get('generated_by') == 'site_studio_workbench_prototype'
        projects.write_file('scenario', SCENARIO, 'webui.json', json.dumps(webui, ensure_ascii=False, separators=(',', ':')) + '\n', max_bytes=1024*1024)
        snapshot = workflow._service().snapshot_current_prototype('scenario', SCENARIO,
            request_text='Owner requested two-pane Site Studio prototype; AdaOS beta only; stop before Automation for human interface review.')
        if args.legacy_webui:
            from site_studio_workbench import REVIEW_SOURCE_RESOURCE, source_review_seed
            from adaos.sdk.developer.prototypes import derive_resource_spec, materialize_resources, carry_forward_resources
            from adaos.services.resources.prototype import PrototypeResourceService
            from adaos.services.resources.prototype_attachments import PrototypeAttachmentStore
            source_records = source_review_seed()
            identity = dict(project_ref='scenario:' + SCENARIO, change_id='source-materials-ui-review', revision=snapshot['revision'], webui=webui)
            if PrototypeResourceService().definition(REVIEW_SOURCE_RESOURCE):
                materialization = carry_forward_resources(**identity)
            else:
                spec = derive_resource_spec(webui, source_records, resource_type=REVIEW_SOURCE_RESOURCE)
                materialization = materialize_resources(**identity, resources=[spec])
            attachments = PrototypeAttachmentStore(PrototypeResourceService())
            for row in source_records:
                receipt = attachments.put(REVIEW_SOURCE_RESOURCE, 'file_ref', row['file_ref'].rsplit('/', 1)[-1], row['content'].encode('utf-8'))
                assert receipt['ref'] == row['file_ref']
            (args.output / 'source-materials.json').write_text(json.dumps(materialization, indent=2) + '\n', encoding='utf-8')
        state = workflow.get_state('scenario', SCENARIO)
        retained = workflow.transition('scenario', SCENARIO, 'prototype_revision_recorded', expected_generation=state['generation'],
            metadata={'revision': snapshot['revision'], 'object_type': 'scenario', 'prototype_acceptance_required': True})
        receipt = {'scenario': SCENARIO, 'snapshot': snapshot, 'generation': retained.get('generation'), 'accepted': False}
        (args.output / 'installation.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
