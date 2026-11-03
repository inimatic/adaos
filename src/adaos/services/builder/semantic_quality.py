"""Bounded non-authorable quality obligations; never a general UI classifier.

Only a whole source clause stating that ALL existing controls remain available
at a width without horizontal overflow is recognized. A model binding cannot
prove this property. Keep the active requirement and its pending runtime check;
do not remove or self-certify it. Other/mixed UI requests stay authorable.
"""
from __future__ import annotations

import re

from .task_spec import source_clauses

_FORMS = (
    re.compile(r'На\s+ширине\s+(?P<width>\d+)\s*px\s+все\s+(?P<controls>[\w\s,–—-]+?)\s+'
               r'должны\s+оставаться\s+доступными\s+без\s+горизонтального\s+переполнения[.!]?', re.I),
    re.compile(r'At\s+(?:width\s+)?(?P<width>\d+)\s*px\s*,?\s*all\s+(?P<controls>[\w\s,–—-]+?)\s+'
               r'must\s+remain\s+(?:available|accessible|usable)\s+without\s+horizontal\s+(?:overflow|scrolling)[.!]?', re.I),
)
_NEW_FEATURE = re.compile(r'\b(?:new|add\w*|creat\w*|show\w*|нов\w*|добав\w*|созда\w*|покаж\w*)\b', re.I)


def responsive_quality_obligations(brief):
    contract = (brief or {}).get('task_contract') or {}
    source = contract.get('source') or {}
    if not source.get('statement'):
        return []
    clauses = {row['id']: row['text'].strip() for row in source_clauses(source['statement'])}
    result = []
    for row in contract.get('requirements', []):
        refs = row.get('clause_ids', [])
        derivation = contract.get('derivation') or {}
        current_sources = {source.get('digest')}
        if derivation.get('kind') == 'exact_source_replay':
            current_sources.add(derivation.get('meaning_source_digest'))
        if (row.get('status') != 'active' or row.get('kind') != 'ui'
                or row.get('verification_family') != 'layout' or len(refs) != 1
                or row.get('source_digest') not in current_sources):
            continue
        text = clauses.get(refs[0], '')
        match = next((match for pattern in _FORMS if (match := pattern.fullmatch(text))), None)
        if not match or _NEW_FEATURE.search(match['controls']):
            continue
        width = int(match['width'])
        for index, constraint in enumerate(contract.get('constraints', [])):
            # Equal active viewport assertions are consolidated by meaning and
            # can retain an earlier citation. The whole quality clause above
            # independently grounds this same width in the current source.
            if constraint.get('kind') != 'viewport':
                continue
            widths = constraint.get('widths_px') or [int(found[0]) for value in constraint.get('values', [])
                if (found := re.findall(r'^\s*(\d+)\s*(?:px)?\s*$', str(value), re.I))]
            if width not in widths:
                continue
            result.append({'id': row['id'], 'statement': row['statement'], 'verification_family': 'layout',
                'verification_owner': 'independent_browser_runtime', 'authoring_binding_required': False,
                'quality_kind': 'responsive_availability',
                'constraint_ref': f'task_acceptance.constraints[{index}]'})
            break
    return result
