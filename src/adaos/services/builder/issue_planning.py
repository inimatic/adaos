"""Lossless bounded Issue planning; text is partitioned, never paraphrased.

The canonical parent remains reviewable and every original criterion has a
stable identity. Executable children obey the existing 20 x 500 ABI. A single
overlong criterion still requires semantic decomposition; cutting prose into
arbitrary substrings would not create independently reviewable obligations.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping


def criterion_id(issue_id, index, text):
    suffix = hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:20]
    return f'criterion:{hashlib.sha256(str(issue_id).encode()).hexdigest()[:16]}:{index}:{suffix}'


def decompose_issues(issues, *, maximum=50, reserved_ids=()):
    result = []
    reserved = set(reserved_ids)
    for index, original in enumerate(issues, 1):
        if not isinstance(original, Mapping):
            raise ValueError('change set issues must be objects')
        row = copy.deepcopy(dict(original))
        identity = str(row.get('issue_id') or row.get('id') or f'I{index:03d}').strip()
        if identity in reserved:
            raise ValueError(f'duplicate change set issue_id: {identity}')
        reserved.add(identity)
        row['issue_id'] = identity
        raw = row.get('acceptance_criteria') or row.get('acceptance') or []
        if not isinstance(raw, (list, tuple)) or len(raw) <= 20:
            result.append(row)
            continue
        if row.get('structural_status', 'active') != 'active' or row.get('source_acceptance_criteria'):
            raise ValueError('only an active, undecomposed Issue can be automatically partitioned')
        if any(not isinstance(text, str) or not text.strip() or len(text) > 500 for text in raw):
            raise ValueError('An original acceptance criterion exceeds 500 characters or is empty; '
                             'decompose its meaning explicitly without dropping source evidence')
        if len(result) + 1 + (len(raw) + 19) // 20 > maximum:
            raise ValueError(f'decomposed change set supports at most {maximum} issues; no criteria accepted')
        children = []
        ids = [criterion_id(identity, i, text) for i, text in enumerate(raw, 1)]
        for offset in range(0, len(raw), 20):
            child_id = f'{identity[:60]}~part{offset // 20 + 1:03d}'
            if child_id in reserved or any(child_id == str(other.get('issue_id') or other.get('id'))
                                           for other in issues if isinstance(other, Mapping)):
                raise ValueError(f'decomposed issue_id collision: {child_id}')
            reserved.add(child_id)
            child = copy.deepcopy(row)
            child.update(issue_id=child_id, title=f'{str(row.get("title") or row.get("summary") or "")[:210]} '
                         f'({offset // 20 + 1}/{(len(raw) + 19) // 20})',
                         acceptance_criteria=list(raw[offset:offset+20]),
                         criterion_ids=ids[offset:offset+20],
                         derived_from_issue_ids=list(dict.fromkeys([*row.get('derived_from_issue_ids', []), identity])))
            child.pop('acceptance', None)
            children.append(child)
        row.update(issue_id=identity, status='deferred', structural_status='split',
                   acceptance_criteria=['Complete every original criterion preserved in the derived Issues.'],
                   source_acceptance_criteria=list(raw), source_criterion_ids=ids,
                   superseded_by_issue_ids=[child['issue_id'] for child in children])
        row.pop('acceptance', None)
        result.extend([row, *children])
    if len(result) > maximum:
        raise ValueError(f'change set supports at most {maximum} issues; no criteria accepted')
    verify_decomposition(result)
    return result


def normalize_provenance(row):
    if ('source_acceptance_criteria' in row) != ('source_criterion_ids' in row):
        raise ValueError('Canonical source text and criterion identities must be retained together')
    result = {}
    for key in ('source_acceptance_criteria', 'source_criterion_ids', 'criterion_ids'):
        if key not in row:
            continue
        value = row[key]
        maximum = 980 if key != 'criterion_ids' else 20
        if (not isinstance(value, list) or not value or len(value) > maximum
                or any(not isinstance(text, str) or not text.strip() or len(text) > 500 for text in value)):
            raise ValueError(f'Invalid lossless Issue provenance: {key}')
        result[key] = list(value)
    return result


def verify_decomposition(issues):
    """Refuse acceptance/reload if a source obligation disappears or is changed."""
    by_id = {str(row['issue_id']): row for row in issues}
    owned_criteria = set()
    for parent in issues:
        raw = parent.get('source_acceptance_criteria')
        if raw is None:
            continue
        if parent.get('structural_status') != 'split':
            raise ValueError('Criterion source must remain an explicitly split parent')
        # The existing workflow ABI canonicalizes whitespace in executable
        # criteria. Retain the verbatim source separately, including newlines.
        expected = {criterion_id(parent['issue_id'], i, text): ' '.join(text.split())
                    for i, text in enumerate(raw, 1)}
        if parent.get('source_criterion_ids') != list(expected):
            raise ValueError('Canonical source criterion identities changed')
        observed = {}
        for child_id in parent.get('superseded_by_issue_ids', []):
            child = by_id.get(child_id)
            if not child or parent['issue_id'] not in child.get('derived_from_issue_ids', []):
                raise ValueError('Lossless decomposition has a missing or unrelated child')
            ids, texts = child.get('criterion_ids', []), child.get('acceptance_criteria', [])
            if len(ids) != len(texts):
                raise ValueError('Every derived criterion requires its canonical source identity')
            for identity, text in zip(ids, texts):
                if identity in observed:
                    raise ValueError('Duplicate derived criterion identity')
                observed[identity] = ' '.join(text.split())
                owned_criteria.add(identity)
        if observed != expected:
            raise ValueError('Original criterion coverage is incomplete or its text changed')
    if any(identity not in owned_criteria for row in issues for identity in row.get('criterion_ids', [])):
        raise ValueError('Derived criterion has no canonical source parent')
