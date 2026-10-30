"""Safe, localized refusal text; model/provider exception prose is not UI policy."""
from collections.abc import Mapping
import re


def interpretation_provider_failure_text(rejection, *, locale='en'):
    """Explain only typed provider evidence, never raw exception prose."""
    from adaos.services.ai.provider_rejection import safe_rejection
    evidence = safe_rejection(rejection)
    kind = (evidence or {}).get('kind')
    russian = str(locale).lower().startswith('ru')
    explanations = {
        'schema':('Провайдер отклонил схему структурированного ответа. Требуется исправление контракта в Core; менять требования приложения не нужно.',
                  'The provider rejected the structured-output schema. Core contract correction is required; do not change application requirements.'),
        'model':('Выбранная модель недоступна у провайдера. Выберите доступную модель ChatGPT.',
                 'The selected model is unavailable from the provider. Select an available ChatGPT model.'),
        'authentication':('Требуется восстановить подключение аккаунта.', 'The account connection must be restored.'),
        'account_scope':('Требуется проверить разрешения подключения аккаунта.', 'The account connection permissions must be checked.'),
        'quota':('Провайдер сообщил об ограничении доступного ресурса аккаунта.', 'The provider reported an account allowance limit.'),
    }
    explanation = explanations.get(kind, ('Провайдер не завершил стадию интерпретации; причина требует проверки сохранённого задания.',
        'The provider did not complete interpretation; inspect the retained attempt to establish the cause.'))[0 if russian else 1]
    suffix = (' Прототип не изменён. Задание сохранено; автоматического повтора и переключения на API нет.' if russian else
        ' The Prototype is unchanged. The attempt is retained; there is no automatic retry or API fallback.')
    return explanation + suffix


def interpretation_refusal_text(result, *, locale='en'):
    if isinstance(result, Mapping) and result.get('error') == 'prototype_task_provider_failed':
        return interpretation_provider_failure_text(result.get('provider_rejection'), locale=locale)
    if not isinstance(result, Mapping) or result.get('error') != 'prototype_task_interpretation_rejected':
        return None
    codes = sorted({row['code'] for row in result.get('findings', []) if isinstance(row, Mapping)
        and isinstance(row.get('code'), str) and re.fullmatch(r'task\.[a-z_]{1,64}', row['code'])})[:12]
    russian = str(locale).lower().startswith('ru')
    if 'task.clarification_required' in codes:
        text = ('До генерации нужно уточнить существенные требования. Прототип не изменён.' if russian else
                'Material requirements need clarification before generation. The Prototype is unchanged.')
    else:
        text = ('Проверка интерпретации отклонила предложение модели. Прототип не изменён; '
                'исходный запрос и ответ сохранены для исправления. Автоматического повторного запроса нет.' if russian else
                'Interpretation admission rejected the model proposal. The Prototype is unchanged; '
                'the source and response are retained for correction. No automatic resubmission was made.')
    if codes:
        text += '\n' + ('Диагностика: ' if russian else 'Diagnostics: ') + ', '.join(codes)
    return text
