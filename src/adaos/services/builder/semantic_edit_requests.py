"""Explicit bounded requests; ambiguity never expands the admitted write scope."""
from __future__ import annotations

import re
from typing import Any, Mapping

from . import semantic_label_requests as labels
from .semantic_graph import reduce_graph

_MOVE = re.compile(
    rf'^(?:move column|перемести (?:колонку|столбец))\s+{labels._QUOTED}'
    rf'\s+(?:before|перед)\s+{labels._QUOTED}'
    rf'\s+(?:in (?:view|table)|в (?:представлении|таблице))\s+{labels._QUOTED}'
    r'(?:\s+(in English|in Russian|на английском|на русском))?\s*[.!]?$', re.I)
_MOVE_PREFIX = re.compile(r'^(?:move column|перемести (?:колонку|столбец))\b', re.I)
_UNDO = re.compile(r'^(?:undo (?:the )?last (semantic|column order) change|'
                   r'отмени последнее (семантическое изменение|изменение порядка колонок))\s*[.!]?$', re.I)


def parse_request(instruction: str) -> dict[str, Any] | None:
    text = instruction.strip()
    existing = labels.parse_request(text)
    if existing is not None:
        return {**existing, **({'undo_family': 'labels'} if existing['kind'] == 'undo' else {})}
    undo = _UNDO.fullmatch(text)
    if undo:
        family = next(value for value in undo.groups() if value)
        return {'kind': 'undo', 'undo_family': 'columns' if family.lower() in
                {'column order', 'изменение порядка колонок'} else 'all'}
    match = _MOVE.fullmatch(text) if len(text) <= 1000 else None
    if match is None:
        return {'kind': 'clarification', 'reason': 'explicit_column_and_view_labels_required'} if _MOVE_PREFIX.match(text) else None
    groups = match.groups()
    values = [next(value for value in groups[start:start + 4] if value is not None) for start in (0, 4, 8)]
    if any(not value.strip() or len(value) > 240 for value in values):
        return {'kind': 'clarification', 'reason': 'label_length_invalid'}
    locale = {'in english': 'en', 'in russian': 'ru', 'на английском': 'en', 'на русском': 'ru'}.get((groups[12] or '').lower())
    return {'kind': 'reorder', 'column': values[0], 'before_column': values[1],
            'view': values[2], 'locale': locale}


def plan(document: Mapping[str, Any], parsed: Mapping[str, Any]) -> dict[str, Any]:
    if parsed['kind'] != 'reorder':
        return labels.plan(document, parsed)
    matches = [(view, locale) for view in document['views'] for locale in ('en', 'ru')
               if (parsed.get('locale') is None or parsed['locale'] == locale)
               and view['title'].get(locale) == parsed['view']]
    if len(matches) != 1:
        return {'status': 'clarification_required', 'reason': 'view_not_found' if not matches else 'ambiguous_view',
                'candidates': [{'ref': f"view:{view['id']}", 'locale': locale} for view, locale in matches]}
    view, locale = matches[0]
    if view['role'] != 'collection' or view.get('presentation') != 'table':
        return {'status': 'clarification_required', 'reason': 'column_order_requires_table', 'candidates': []}
    resources = [resource for resource in document['resources'] if resource['id'] == view['resource_ref']]
    fields = resources[0]['fields']
    def resolve(label):
        return [token for token in view['field_refs'] for field in fields
                if (token == field['id'] or f"{view['resource_ref']}.{token}" == field['id']
                    or token == f"{view['resource_ref']}.{field['id']}")
                and field['label'].get(locale) == label]
    moving, before = resolve(parsed['column']), resolve(parsed['before_column'])
    if len(moving) != 1 or len(before) != 1:
        return {'status': 'clarification_required', 'reason': 'column_not_found_or_ambiguous', 'candidates': []}
    order = list(view['field_refs'])
    if moving[0] != before[0]:
        order.remove(moving[0])
        order.insert(order.index(before[0]), moving[0])
    if order == view['field_refs']:
        return {'status': 'no_change', 'reason': 'column_order_already_matches', 'candidates': []}
    op = {'seq': 1, 'kind': 'view.fields.reorder', 'ref': f"view:{view['id']}",
          'expected': list(view['field_refs']), 'field_refs': order}
    reduced = reduce_graph(document, [op], write_refs=[op['ref']])
    return {'status': 'planned', 'ops': [op], 'write_refs': [op['ref']], 'semantic_digest': reduced['semantic_digest']}
