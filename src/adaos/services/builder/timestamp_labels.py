"""Bounded EN/RU timestamp-caption grammar, not a natural-language judge.

The field's source identity/value and visibility are checked independently.
This recognizes compositional date/time heads, participles and `when` questions;
unknown constructions remain unresolved, never certified by a substring match.
"""
from __future__ import annotations

import re

GRAMMAR = 'display-grammar:timestamp-caption-v1'
_WORD = r'[a-zа-яё]+(?:-[a-zа-яё]+)*'
_ENTITY = rf'{_WORD}(?:\s+{_WORD}){{0,3}}'
_TAIL = rf'(?:\s+(?:of\s+(?:the\s+)?)?{_ENTITY})?'
_HEAD = r'(?:дата|время|дата и время|момент|date|time|date and time|timestamp)'
_PATTERNS = {
    'creation_timestamp': (
        rf'{_HEAD}\s+(?:создания|creation){_TAIL}',
        r'(?:создан(?:а|о|ы)?|created)(?:\s+(?:at|on))?',
        r'(?:когда\s+создан(?:а|о|ы)?|when\s+(?:(?:was|is)\s+)?created)',
        rf'{_ENTITY}\s+(?:created\s+(?:at|on)|creation\s+(?:date|time))',
        r'creation\s+(?:date|time)',
    ),
    'verification_timestamp': (
        rf'{_HEAD}\s+(?:проверки|verification){_TAIL}',
        r'(?:проверен(?:а|о|ы)?|checked|verified)(?:\s+(?:at|on))?',
        r'(?:когда\s+проверен(?:а|о|ы)?|when\s+(?:(?:was|is)\s+)?(?:checked|verified))',
        rf'{_ENTITY}\s+(?:(?:checked|verified)\s+(?:at|on)|verification\s+(?:date|time))',
        r'verification\s+(?:date|time)',
    ),
}
_CUES = {
    'creation_timestamp': re.compile(r'(?<![a-zа-яё0-9_])(?:создания|создан(?:а|о|ы)?|creation|created)(?![a-zа-яё0-9_])'),
    'verification_timestamp': re.compile(r'(?<![a-zа-яё0-9_])(?:проверки|проверен(?:а|о|ы)?|verification|checked|verified)(?![a-zа-яё0-9_])'),
}
_OPERATORS = re.compile(r'(?<![a-zа-яё0-9_])(?:не|нет|или|либо|без|после|до|вместо|not|no|or|without|after|before|instead)(?![a-zа-яё0-9_])')


def label_policy():
    return {'grammar_ref': GRAMMAR, 'meaning_owner': 'source-bound field, not candidate caption or field ID',
        'caption_policy': 'Not an exact label allowlist. EN/RU date/time heads plus creation/verification, '
            'participles (Created / Создано), and When created / Когда создана are supported. '
            'Date/time heads may qualify an entity with up to four words; English entity + creation date is also supported. Negated, alternative or mixed '
            'timestamp meanings are not accepted. Other constructions require explicit review; they are '
            'unresolved, not evidence of a wrong user requirement or permission for a paid model repair.',
        'examples': {'creation_timestamp': ['Дата создания копии', 'Когда создана', 'Backup creation date'],
                     'verification_timestamp': ['Дата проверки копии', 'Когда проверена', 'Verified at']},
        'general_label_semantics_proven': False}


def caption_grammar():
    """Same bounded recognition policy for Core and the browser oracle."""
    return {'grammar_ref': GRAMMAR, 'patterns': {key:list(value) for key,value in _PATTERNS.items()},
        'cues': {key:value.pattern for key,value in _CUES.items()}, 'operators': _OPERATORS.pattern}


def classify_caption(label):
    text = re.sub(r'\s+', ' ', str(label)).strip().rstrip(':?').strip().casefold()
    cues = [meaning for meaning, expression in _CUES.items() if expression.search(text)]
    if len(cues) > 1 or _OPERATORS.search(text):
        return {'status': 'unresolved', 'reason': 'timestamp_caption_polarity_or_alternatives', 'grammar_ref': GRAMMAR}
    matches = [meaning for meaning, patterns in _PATTERNS.items()
               if any(re.fullmatch(pattern, text) for pattern in patterns)]
    if len(matches) != 1:
        return {'status': 'unresolved', 'reason': 'timestamp_caption_outside_grammar', 'grammar_ref': GRAMMAR}
    return {'status': 'recognized', 'meaning': matches[0], 'grammar_ref': GRAMMAR}
