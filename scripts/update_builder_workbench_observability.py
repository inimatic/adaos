"""Surgical Builder-owned migration of current live sources, not design regeneration."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

def write(path, value):
    # The live Builder owns a four-space source format, unlike archived design
    # fixtures. Preserve it so the migration stays reviewable.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=4) + '\n', encoding='utf-8', newline='\n')


def localized(label, ru, *, key=None, **extra):
    value = {'label': label, 'label_i18n': {'locales': {'en': label, 'ru': ru}}, **extra}
    if key:
        value['key'] = key
    return value


def upgrade(document):
    result = copy.deepcopy(document)
    app = result['ui']['application']
    resources = app.setdefault('resources', {})
    for locale in ('en', 'ru'):
        for name, filename in [('design', f'design-071-{locale}.json'), ('live', f'workbench-live-{locale}.json')]:
            resources[f'builder.{name}.i18n.{locale}'] = {
                'kind': 'data', 'role': 'i18n', 'locale': locale, 'scope': 'scenario',
                'path': f'assets/i18n/{filename}', 'mime': 'application/json', 'delivery': 'core'}
    # Preserve the compatibility declaration without substituting a different
    # dictionary namespace for the application's effective resources.
    if 'resources' in result:
        result['resources'] = copy.deepcopy(resources)
    page = app['desktop']['pageSchema']
    selection_source = page.get('initialStateSource') or {}
    if 'mapping' in selection_source:
        if 'map' in selection_source and selection_source['map'] != selection_source['mapping']:
            raise ValueError('Conflicting Builder selection source mappings')
        selection_source['map'] = selection_source.pop('mapping')
    def pin_chat_target(value):
        if isinstance(value, dict):
            if value.get('type') == 'ui.chat' and value.get('inputs', {}).get('sendCommand') == 'voice.chat.user':
                value['inputs'].setdefault('meta', {})['builder_selected_target'] = {
                    'object_type': '$state.selectedProjectKind', 'object_id': '$state.selectedProjectId'}
            for child in value.values():
                pin_chat_target(child)
        elif isinstance(value, list):
            for child in value:
                pin_chat_target(child)
    pin_chat_target(page)
    table = next(widget for widget in page['widgets'] if widget['id'] == 'design-trace-tasks')
    table['inputs']['columns'] = [
        localized('Run / action', 'Запуск / действие', key='title'),
        localized('Started', 'Начало', key='started_at', kind='datetime'),
        localized('Revision', 'Редакция', key='revision'),
        localized('Status', 'Статус', key='status'),
        localized('Model', 'Модель', key='model_label'),
        localized('Input tokens', 'Входные токены', key='input_tokens_label'),
        localized('Output tokens', 'Выходные токены', key='output_tokens_label'),
        localized('Repairs', 'Ремонты', key='repair_attempts_label'),
        localized('Cost USD', 'Стоимость USD', key='cost_label'),
    ]
    table['inputs']['compactPresentation'] = 'cards'
    table['inputs']['selectionMode'] = 'single'
    for action in table.get('actions', []):
        if action.get('type') == 'openModal':
            action['params']['modalId'] = 'design-run-details'
    fields = [
        ('title', 'Run / action', 'Запуск / действие'), ('id', 'Run ID', 'ID запуска'),
        ('change_id', 'Change', 'Изменение'), ('status', 'Status', 'Статус'),
        ('revision', 'Result revision', 'Редакция результата'), ('executor', 'Executor', 'Исполнитель'),
        ('started_at', 'Started', 'Начало'), ('completed_at', 'Completed', 'Завершение'),
        ('model_label', 'Model', 'Модель'), ('effort_label', 'Requested reasoning effort', 'Запрошенная интенсивность reasoning'),
        ('llm_calls_label', 'LLM calls', 'LLM-вызовы'), ('repair_attempts_label', 'Repair calls', 'Вызовы ремонта'),
        ('input_tokens_label', 'Input tokens (including cached)', 'Входные токены (включая кеш)'),
        ('cached_input_tokens_label', 'Cached input tokens', 'Кешированные входные токены'),
        ('output_tokens_label', 'Output tokens (including reasoning)', 'Выходные токены (включая reasoning)'),
        ('reasoning_tokens_label', 'Reasoning tokens', 'Токены reasoning'),
        ('cost_label', 'Cost USD and basis', 'Стоимость USD и основание'),
        ('root_time_label', 'Root execution time (not pure model time)', 'Время исполнения Root (не чистое время модели)'),
        ('root_queue_ms_label', 'Root queue time, ms', 'Очередь Root, мс'),
        ('client_wait_ms_label', 'Client observation wait, ms', 'Ожидание клиентского наблюдателя, мс'),
        ('measurement_label', 'Execution measurement coverage', 'Наличие измерений исполнения'),
        ('error', 'Failure', 'Ошибка'),
    ]
    context = [
        ('request', 'Workflow request (not a reconstructed conversation)', 'Запрос workflow (не восстановленная переписка)'),
        ('source_message_ids', 'Linked conversation messages', 'Связанные сообщения обсуждения'),
        ('context_packet_digest', 'Recorded context digest', 'Дайджест сохранённого контекста'),
        ('input_refs', 'Inputs', 'Исходные данные'), ('output_refs', 'Outputs', 'Результаты'),
        ('evidence_refs', 'Evidence', 'Доказательства'), ('environment_ref', 'Environment', 'Окружение'),
        ('execution_metrics.source_refs', 'Measurement sources', 'Источники измерений'),
    ]
    modal = copy.deepcopy(app['modals']['design-trace'])
    modal.update(title='Run details', title_i18n={'locales': {'en': 'Run details', 'ru': 'Детали запуска'}})
    modal['schema']['id'] = 'design-run-details'
    base = modal['schema']['widgets'][0]
    base['id'] = 'design-run-execution'
    base.update(title='Execution', title_i18n={'locales': {'en': 'Execution', 'ru': 'Исполнение'}})
    # Unknown historical metrics belong to one explicit coverage statement, not
    # a screenful of empty token/cost rows above the useful input evidence.
    summary_keys = {'title', 'status', 'revision', 'executor', 'started_at', 'completed_at', 'measurement_label', 'error'}
    base['inputs']['fields'] = [localized(en, ru, key=key) for key, en, ru in fields if key in summary_keys]
    base['inputs']['emptyText'] = 'No recorded execution details.'
    details = copy.deepcopy(base)
    details['id'] = 'design-run-context'
    details.update(title='Inputs and evidence', title_i18n={'locales': {'en': 'Inputs and evidence', 'ru': 'Входные данные и доказательства'}})
    details['inputs']['fields'] = [localized('Run ID', 'ID запуска', key='id'), localized('Change', 'Изменение', key='change_id'),
                                  *[localized(en, ru, key=key) for key, en, ru in context]]
    measurements = copy.deepcopy(base)
    measurements['id'] = 'design-run-measurements'
    measurements.update(title='Recorded measurements', title_i18n={'locales': {'en': 'Recorded measurements', 'ru': 'Сохранённые измерения'}})
    measurements['visibleIf'] = '$state.selectedTrace.execution_metrics'
    measurements['inputs']['fields'] = [localized(en, ru, key=key) for key, en, ru in fields
                                        if key not in summary_keys and key not in {'id', 'change_id'}]
    modal['schema']['widgets'] = [base, details, measurements]
    app['modals']['design-run-details'] = modal
    notice = {
        'id': 'design-conversation-request-provenance', 'type': 'item.details', 'area': 'main',
        'title': 'Workflow request without linked discussion',
        'title_i18n': {'locales': {'en': 'Workflow request without linked discussion', 'ru': 'Запрос workflow без связанных сообщений обсуждения'}},
        'visibleIf': "$state.workbenchView === 'conversation' && $state.workbench.source_message_count === 0 && $state.workbench.request",
        'dataSource': {'kind': 'static', 'value': '$state.workbench'},
        'inputs': {'presentation': 'section', 'fields': [
            localized('Recorded request', 'Сохранённый запрос', key='request'),
            localized('Change', 'Изменение', key='change_id')]},
    }
    page['widgets'] = [widget for widget in page['widgets'] if widget['id'] != notice['id']]
    page['widgets'].append(notice)
    # Root is the availability authority; old inline choices must not reappear
    # when the dynamic catalogue fails or advertises no usable model.
    settings = next(widget for widget in app['modals']['design-settings']['schema']['widgets'] if widget['id'] == 'chat-side-settings')
    for field in settings['inputs']['fields']:
        if field['id'] == 'llmModel':
            field['options'] = []
    return result


def caption_keys(value):
    result = set()
    if isinstance(value, dict):
        for name, child in value.items():
            if name.endswith('_i18n'):
                key = child if isinstance(child, str) else child.get('key') if isinstance(child, dict) else None
                if key and not key.startswith('$'):
                    result.add(key)
            result.update(caption_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(caption_keys(child))
    return result


def audit_locales(document, root):
    resources = document['ui']['application']['resources']
    expected = caption_keys(document)
    coverage = {}
    for locale in ('en', 'ru'):
        dictionary = {}
        for resource in resources.values():
            if resource.get('role') == 'i18n' and resource.get('locale') == locale:
                path = root / resource['path']
                if not path.is_file():
                    raise ValueError(f'Missing declared {locale} dictionary: {path}')
                dictionary.update(json.loads(path.read_text(encoding='utf-8-sig')))
        missing = sorted(expected - dictionary.keys())
        if missing:
            raise ValueError(f'Missing {locale} captions: {missing}')
        coverage[locale] = len(expected)
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if root.name != 'builder':
        raise ValueError('This migration owns only the Builder scenario')
    ui = upgrade(json.loads((root / 'webui.json').read_text(encoding='utf-8-sig')))
    coverage = audit_locales(ui, root)
    if args.apply:
        write(root / 'webui.json', ui)
        scenario_path = root / 'scenario.json'
        if scenario_path.is_file():
            scenario = json.loads(scenario_path.read_text(encoding='utf-8-sig'))
            # One authoritative UI source; do not duplicate the whole document
            # into a scenario that already resolves webui.json by manifest.
            scenario['ui'] = {'manifest': 'webui.json'}
            write(scenario_path, scenario)
    print(json.dumps({'root': str(root), 'applied': args.apply, 'caption_coverage': coverage}))


if __name__ == '__main__':
    main()
