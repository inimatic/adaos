"""Safe, localized refusal text; model/provider exception prose is not UI policy."""
from collections.abc import Mapping
import re


def interpretation_refusal_text(result, *, locale='en'):
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
