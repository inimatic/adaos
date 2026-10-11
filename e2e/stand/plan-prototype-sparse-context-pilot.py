"""Freeze compact held-out tasks before any optional, separately budgeted calls."""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import compile_experiment_messages, native_builder, read, source_fingerprints
from adaos.sdk.builder import intent, prototype
from adaos.services.agent_context import get_ctx
from adaos.services.builder_context import digest

load_dotenv()
logging.disable(logging.WARNING)
assert os.getenv('ENV_TYPE') == 'dev'
init_ctx(Settings.from_sources())
output = Path('e2e/artifacts/builder/prototype-sparse-context-pilot-20261011-01')
output.mkdir(exist_ok=True)
builder = native_builder(Path(get_ctx().paths.dev_skills_dir()) / 'builder_skill/handlers/main.py')
initial = read(Path('src/adaos/scenario_templates/scenario_default/webui.json'))
tasks = [
    {'id': 'book-catalog-ru', 'instruction': 'Локальный прототип каталога книг на русском языке в режиме чтения. Единственная сущность — книги: название, автор, жанр, год издания. Главный элемент — таблица. Поиск по названию и фильтр по жанру должны менять видимые строки. Подготовь четыре реалистичные записи двух жанров и проверяемый пример отсутствия результатов поиска. На телефоне содержимое должно оставаться читаемым без горизонтального переполнения.'},
    {'id': 'contact-form-ru', 'instruction': 'Локальный прототип адресной книги на русском языке. Единственная сущность — контакты: имя и адрес электронной почты. Пользователь видит список двух примеров и может создавать и редактировать контакт через отдельную форму. Имя обязательно: незаполненное имя блокирует сохранение с понятным сообщением. Отмена формы сохраняет прежние данные; успешное сохранение меняет локальную запись. На телефоне форма и список должны оставаться читаемыми без горизонтального переполнения.'},
]
registration = {'schema': 'adaos.e2e.sparse_context_pilot.v1', 'additional_usd_limit': 8,
    'models': ['gpt-5', 'gpt-6-astra'], 'reasoning_effort': 'high', 'arms': ['full-mcp', 'task-aware'],
    'maximum_primary_requests': 8, 'maximum_output_tokens': 24576, 'max_tool_calls': 4,
    'provider_max_attempts': 1, 'planned_only': True, 'source_fingerprints': source_fingerprints(Path.cwd()),
    'scope': 'Separate compact held-out task pilot; NOT pooled with rich five-arm series. Equal output/tool limits within paired arms. No paid Brief compilation.',
    'tasks': []}
for task in tasks:
    brief = intent.compile_brief(task['instruction'])
    schema = prototype.semantic_provider_contract(version='v2', locales=('ru',), brief=brief)
    session = {'id': 'scenario.test_sparse_context', 'scenario_id': 'test_sparse_context',
        'title': task['id'], 'source_idea': task['instruction'], 'project_ref': 'project:test_sparse_context',
        'ui_revision': '001', 'webui_payload': copy.deepcopy(initial), 'builder_llm_model': 'gpt-5'}
    request = builder._builder_llm_webui_transform_request(session=session, instruction=task['instruction'],
        preview_state=initial.get('preview_state') or {}, _meta={'locale': 'ru', 'prototype_brief': brief,
            'builder_e2e_generation_contract': 'semantic.v2', 'builder_semantic_compiler': True})
    messages, purposes = builder._builder_llm_messages(request, request['user_prompt'])
    directory = output / task['id']
    directory.mkdir(exist_ok=True)
    _write_json(directory / 'brief.json', brief)
    task_plan = {**task, 'brief_sha256': digest(brief), 'schema_sha256': digest(schema), 'arms': {}}
    for arm in registration['arms']:
        options = {'text': {'verbosity': 'low', 'format': {'type': 'json_schema', 'name': 'adaos_builder_semantic_prototype_candidate', 'strict': True, 'schema': schema}},
            'max_tokens': registration['maximum_output_tokens'], 'reasoning': {'effort': 'high'}}
        projected, receipt = compile_experiment_messages(messages, strategy=arm, generation_options=options,
            context_mode='composition', mcp_available=True)
        packet = {'messages': projected, 'options': options, 'purposes': purposes, 'receipt': receipt}
        path = directory / ('planned-' + arm + '.json')
        if path.exists() and read(path) != packet:
            raise ValueError('Frozen sparse pilot drift')
        _write_json(path, packet)
        task_plan['arms'][arm] = {k: receipt.get(k) for k in ('input_chars', 'output_chars', 'estimated_tokens', 'task_selections')}
    registration['tasks'].append(task_plan)
    print(json.dumps({'task': task['id'], 'full_chars': task_plan['arms']['full-mcp']['output_chars'],
        'task_aware_chars': task_plan['arms']['task-aware']['output_chars'],
        'inline_fragments': len(task_plan['arms']['task-aware']['task_selections'][0]['inline_fragment_ids'])}))
path = output / 'preregistration.json'
if path.exists() and read(path) != registration:
    raise ValueError('Frozen sparse pilot registration drift')
_write_json(path, registration)
