"""Bounded provider rejection evidence; never persist or display raw bodies."""
from __future__ import annotations

import hashlib
import json
import re
import httpx

_KINDS = frozenset({'authentication', 'quota', 'reasoning', 'schema', 'tools', 'model',
                    'account_scope', 'access', 'provider_unavailable', 'request'})


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.\[\]-]{1,160}', value) else None


def safe_rejection(value):
    """Keep only a typed diagnostic even when an executor raises custom errors."""
    if (not isinstance(value, dict) or not isinstance(value.get('kind'), str)
            or value['kind'] not in _KINDS or type(value.get('http_status')) is not int):
        return None
    if not 100 <= value['http_status'] <= 599:
        return None
    result = {'kind': value['kind'], 'http_status': value['http_status']}
    for key in ('code', 'param'):
        token = _identifier(value.get(key))
        if token:
            result[key] = token
    if re.fullmatch(r'sha256:[0-9a-f]{64}', str(value.get('body_digest') or '')):
        result['body_digest'] = value['body_digest']
    return result


def response_rejection(response, *, web_search=False):
    body = bytearray()
    complete = True
    try:
        for chunk in response.iter_bytes(chunk_size=2048):
            if len(body) + len(chunk) > 8192:
                complete = False
                break
            body.extend(chunk)
        data = json.loads(body) if complete else {}
    except (ValueError, OSError, httpx.HTTPError):
        data = {}
    error = data.get('error') if isinstance(data, dict) else {}
    error = error if isinstance(error, dict) else {}
    code, param = _identifier(error.get('code')), _identifier(error.get('param'))
    status = response.status_code
    kind = 'request'
    if status == 401:
        kind = 'authentication'
    elif status == 429:
        kind = 'quota'
    elif status >= 500:
        kind = 'provider_unavailable'
    elif code == 'insufficient_scope':
        kind = 'account_scope'
    elif param and (param == 'reasoning' or param.startswith('reasoning.')):
        kind = 'reasoning'
    elif param and (param.startswith('text.format') or param.startswith('response_format')):
        kind = 'schema'
    elif param and (param.startswith('tools') or param in {'tool_choice', 'web_search'}):
        kind = 'tools'
    elif param == 'model' or code in {'model_not_found', 'unsupported_model', 'model_not_available'}:
        kind = 'model'
    elif not param and web_search and isinstance(error.get('message'), str) and re.search(
        r'unsupported (?:tool|web.search)\b', error['message'], re.I
    ):
        # Legacy gateways omit param. Only explicit tool evidence qualifies;
        # enabling an option is never evidence that it caused the rejection.
        kind = 'tools'
    elif status == 403:
        kind = 'access'
    result = {'http_status': status, 'kind': kind, 'code': code, 'param': param}
    if complete:
        result['body_digest'] = 'sha256:' + hashlib.sha256(body).hexdigest()
    return safe_rejection(result)
