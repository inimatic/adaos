"""Shared advertised execution choices; model identity includes its funding route."""
from __future__ import annotations
from collections.abc import Mapping
import re


def execution_choices(root_catalog, personal_models=(), *, binding=None):
    result = []
    rows = root_catalog.get('data', [])
    if not isinstance(rows, list):
        rows = []
    profiles = root_catalog.get('model_profiles') or root_catalog.get('dev_model_profiles') or []
    # A scoped Root catalog is authoritative. Do not replace it with a static list.
    for row in [*rows, *profiles]:
        if not isinstance(row, Mapping) or not (row.get('id') or row.get('model')):
            continue
        model = row.get('id') or row['model']
        # The provider's /models also advertises speech, images and embeddings.
        # They are not execution choices for a structured text authoring turn.
        if re.search(r'(?:image|audio|transcribe|realtime|embedding|moderation|tts|whisper|sora|live-)', model, re.I):
            continue
        result.append({'id':'root::'+model, 'model':model, 'transport':'root',
            'label':str(row.get('label') or model)+' · API', 'available':row.get('available', True) is not False,
            'capabilities':dict(row.get('capabilities') or {}),
            'supported_reasoning_efforts':row.get('supported_reasoning_efforts') or [],
            'pricing':row.get('pricing'), 'funding_source':'platform_api'})
    if binding:
        for row in personal_models:
            if not isinstance(row, Mapping) or not row.get('id'):
                continue
            result.append({'id':'personal::'+row['id'], 'model':row['id'], 'transport':'personal',
                'label':str(row.get('label') or row['id'])+' · ChatGPT',
                'available':row.get('available', True) is not False,
                'capabilities':dict(row.get('capabilities') or {}),
                'supported_reasoning_efforts':(row.get('capabilities') or {}).get('efforts') or [],
                'funding_source':'personal_subscription', 'pricing':None, 'binding_ref':binding})
    return list({row['id']:row for row in result}.values())


def validate_choice(catalog, choice_id, effort='default', *, subscription_output_accepted=False):
    item = next((row for row in catalog if row['id']==choice_id and row['available']), None)
    if not item:
        raise ValueError('Выбранная модель недоступна. Обновите список моделей.')
    if item['transport']=='personal' and subscription_output_accepted is not True:
        raise ValueError('Подтвердите использование плана ChatGPT с лимитами ответа провайдера.')
    reasoning = None
    if effort != 'default':
        supported = item['supported_reasoning_efforts']
        if supported:
            if effort not in supported:
                raise ValueError('Этот уровень рассуждения не поддерживается выбранной моделью.')
            reasoning = {'effort':effort}
        elif item['transport']=='root':
            from .model_controls import reasoning_for_model
            reasoning = reasoning_for_model(item['model'], effort)
            if reasoning is None:
                raise ValueError('Модель не объявила настройку уровня рассуждения.')
        else:
            raise ValueError('Провайдер не объявил доступные уровни рассуждения.')
    return {**item, 'reasoning':reasoning}
