"""Bounded provider rejection evidence; never persist or display raw bodies."""
from __future__ import annotations

import hashlib
import json
import re
import ast
import httpx

_KINDS = frozenset({'authentication', 'quota', 'reasoning', 'schema', 'tools', 'model',
                    'account_scope', 'access', 'provider_unavailable', 'request'})
_KEYWORDS = frozenset({'type', 'properties', 'required', 'additionalProperties', 'items', 'anyOf',
    'oneOf', 'allOf', 'not', 'if', 'then', 'else', 'enum', 'const', 'pattern', 'format', 'minimum',
    'maximum', 'minItems', 'maxItems', 'minLength', 'maxLength', 'uniqueItems', 'dependentRequired',
    'dependentSchemas', 'patternProperties', '$ref', '$defs'})
_SCHEMA_TERMS = frozenset(word.lower() for word in _KEYWORDS) | frozenset({
    'object', 'objects', 'array', 'arrays', 'string', 'strings', 'number', 'null', 'boolean',
    'schema', 'schemas', 'must', 'not', 'be', 'have', 'has', 'share', 'identical', 'first',
    'key', 'keys', 'property', 'provided', 'via', 'consider', 'moving', 'common', 'out', 'of',
    'missing', 'invalid', 'unsupported', 'supported', 'permitted', 'only', 'all', 'every',
    'unique', 'duplicate', 'empty', 'false', 'true', 'exceeds', 'maximum', 'minimum', 'limit',
    'too', 'many', 'total', 'length', 'characters', 'values', 'value', 'nesting', 'depth'})


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
    path = value.get('schema_path')
    if (isinstance(path, list) and len(path) <= 24 and all(
        type(token) is int and 0 <= token <= 5000 or isinstance(token, str)
        and re.fullmatch(r'[A-Za-z0-9_.$\[\]-]{1,96}', token) for token in path
    )):
        result['schema_path'] = list(path)
    keywords = value.get('schema_keywords')
    if isinstance(keywords, list) and len(keywords) <= len(_KEYWORDS) and all(
        isinstance(token, str) and token in _KEYWORDS for token in keywords
    ):
        result['schema_keywords'] = sorted(set(keywords))
    terms = value.get('schema_terms')
    if isinstance(terms, list) and len(terms) <= 96 and all(
        isinstance(token, str) and token in _SCHEMA_TERMS for token in terms
    ):
        result['schema_terms'] = list(terms)
    return result


def _schema_evidence(message, schema):
    """Retain only a path that actually resolves inside the submitted schema.

    Never retain quoted source literals, schema enum/const values or arbitrary
    provider prose. Keyword tokens come from a fixed public vocabulary.
    """
    if not isinstance(message, str) or not isinstance(schema, dict):
        return {}
    match = re.search(r'\bcontext=(\([^\n)]{0,1024}\))', message)
    diagnostic = message[match.end():] if match else message
    result = {'schema_keywords': sorted(token for token in _KEYWORDS if
        re.search(r"['\"]" + re.escape(token) + r"['\"]", diagnostic))}
    result['schema_terms'] = [token.lower() for token in re.findall(r'[A-Za-z_$][A-Za-z0-9_$]*', diagnostic)
                              if token.lower() in _SCHEMA_TERMS][:96]
    if not match:
        return result
    try:
        path = ast.literal_eval(match[1])
        if not isinstance(path, tuple) or len(path) > 24:
            return result
        node = schema
        for token in path:
            if token in ('const', 'enum', 'description', 'examples', 'default'):
                return result  # Source-owned literal content is not a schema location diagnostic.
            visited = set()
            # Providers can report a resolved schema location through a local
            # $ref rather than the physical $defs path. Resolve only existing
            # local pointers, with a cycle bound; never fetch remote content.
            while isinstance(node, dict) and '$ref' in node and not (isinstance(token, str) and token in node):
                ref = node['$ref']
                if not isinstance(ref, str) or not ref.startswith('#/') or ref in visited:
                    return result
                visited.add(ref)
                node = schema
                for part in ref[2:].split('/'):
                    part = part.replace('~1', '/').replace('~0', '~')
                    if not isinstance(node, dict) or part not in node:
                        return result
                    node = node[part]
            if type(token) is int and isinstance(node, list) and 0 <= token < len(node):
                node = node[token]
            elif isinstance(token, str) and isinstance(node, dict) and token in node:
                node = node[token]
            else:
                return result
        result['schema_path'] = list(path)
    except (ValueError, SyntaxError, TypeError, RecursionError):
        pass
    return result


def response_rejection(response, *, web_search=False, output_schema=None):
    body = bytearray()
    complete = True
    try:
        for chunk in response.iter_bytes(chunk_size=2048):
            if len(body) + len(chunk) > 8192:
                complete = False
                break
            body.extend(chunk)
        data = json.loads(body) if complete else {}
    except ValueError:
        data = {}
    except (OSError, httpx.HTTPError):
        data, complete = {}, False
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
    if kind == 'schema':
        result.update(_schema_evidence(error.get('message'), output_schema))
    if complete:
        result['body_digest'] = 'sha256:' + hashlib.sha256(body).hexdigest()
    return safe_rejection(result)
