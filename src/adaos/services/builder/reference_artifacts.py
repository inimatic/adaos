"""Immutable, project-scoped design reference text. Content is never executed."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any
from adaos.services.context_control import ContextControlService

SCHEMA = 'adaos.builder.reference_text.v1'
MAX_FILE_BYTES = 262144
MAX_TOTAL_BYTES = 524288
MEDIA_TYPES = frozenset({'text/html', 'text/css', 'text/javascript', 'application/javascript',
                         'application/json', 'text/plain', 'text/markdown'})


def _project(value):
    if not isinstance(value, str) or not re.fullmatch(r'(scenario|skill|project):[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('A concrete Builder project reference is required')


class BuilderReferenceArtifacts:
    def __init__(self, *, state_dir=None):
        self.store = ContextControlService(state_dir=state_dir)

    def add(self, *, project_ref: str, name: str, media_type: str, text: str) -> dict[str, Any]:
        _project(project_ref)
        if (not isinstance(name, str) or not name or len(name) > 160 or name in {'.', '..'}
                or PurePosixPath(name).name != name or '\\' in name or ':' in name
                or any(ord(c) < 32 for c in name)):
            raise ValueError('Reference name must be a plain file name')
        if media_type not in MEDIA_TYPES:
            raise ValueError('Unsupported reference text media type')
        if not isinstance(text, str) or not text or len(text.encode('utf-8')) > MAX_FILE_BYTES:
            raise ValueError('Reference text must be nonempty UTF-8 within 256 KiB')
        value = {'schema': SCHEMA, 'project_ref': project_ref, 'name': name,
                 'media_type': media_type, 'text': text,
                 'content_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                 'authority': 'reference_only', 'execution': 'never'}
        receipt = self.store.put_artifact(value)
        return {**receipt, 'project_ref': project_ref, 'name': name, 'media_type': media_type,
                'content_sha256': value['content_sha256'], 'content_bytes': len(text.encode('utf-8'))}

    def resolve(self, *, project_ref: str, receipts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        _project(project_ref)
        if not isinstance(receipts, (list, tuple)) or len(receipts) > 8:
            raise ValueError('At most eight explicit reference receipts are supported')
        result = []; seen = set(); total = 0
        for receipt in receipts:
            if not isinstance(receipt, Mapping):
                raise ValueError('Invalid reference receipt')
            ref = receipt.get('ref')
            if not isinstance(ref, str) or not ref.startswith('artifact://context/sha256/'):
                raise ValueError('A retained reference receipt is required')
            if receipt.get('digest') != 'sha256:' + ref.rsplit('/', 1)[-1]:
                raise ValueError('Reference receipt identity mismatch')
            value = self.store.get_artifact(ref)
            if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('project_ref') != project_ref:
                raise ValueError('Reference belongs to another project or artifact type')
            text = value.get('text')
            if (not isinstance(text, str) or value.get('media_type') not in MEDIA_TYPES
                    or value.get('authority') != 'reference_only' or value.get('execution') != 'never'
                    or value.get('media_type') != receipt.get('media_type')
                    or hashlib.sha256(text.encode('utf-8')).hexdigest() != receipt.get('content_sha256')
                    or value.get('content_sha256') != receipt.get('content_sha256')
                    or value.get('name') != receipt.get('name') or receipt.get('project_ref') != project_ref
                    or len(text.encode('utf-8')) != receipt.get('content_bytes')
                    or len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8')) != receipt.get('bytes')):
                raise ValueError('Reference content identity mismatch')
            if ref in seen:
                continue
            total += len(text.encode('utf-8'))
            if len(text.encode('utf-8')) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError('Reference context exceeds its explicit byte allowance')
            seen.add(ref)
            result.append({**value, 'receipt': dict(receipt)})
        return result
