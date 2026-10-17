"""Explicit, revision-bound authorization for a requested Automation UI delta."""
from __future__ import annotations
from collections.abc import Mapping
from .traceability import digest


def admit_ui_change(value, *, project_id, prototype_revision):
    if not isinstance(value, Mapping) or set(value) != {'reason','source_message_ids'}:
        raise ValueError('UI change requires an explicit reason and canonical source message ids')
    reason, refs = value['reason'], value['source_message_ids']
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > 4000
            or not isinstance(refs, list) or not 1 <= len(refs) <= 20
            or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 300 for ref in refs)):
        raise ValueError('UI change authorization is empty or exceeds its admitted bounds')
    if not project_id or not prototype_revision:
        raise ValueError('UI change authorization requires the accepted project revision')
    body = {'schema': 'adaos.builder.ui_change_authorization.v1', 'authority': 'builder.automation',
        'project_id': project_id, 'base_prototype_revision': prototype_revision,
        'allowed_paths': [f'scenarios/{project_id}/webui.json', f'scenarios/{project_id}/semantic.webui.json'],
        'reason': reason.strip(), 'source_message_ids': list(dict.fromkeys(refs))}
    return {**body, 'digest': digest(body)}


def authorized_ui_change(constraints, *, project_id, prototype_revision):
    value = (constraints or {}).get('ui_change_authorization')
    if not isinstance(value, Mapping):
        return None
    try:
        expected = admit_ui_change({key: value[key] for key in ('reason','source_message_ids')},
            project_id=project_id, prototype_revision=prototype_revision)
    except (ValueError, KeyError):
        return None
    return expected if dict(value) == expected else None
