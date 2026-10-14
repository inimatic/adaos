"""Read-only live EU/RU qualification of a published common Root projection.

Issues a short least-privilege read lease; no LLM requests or runtime writes.
Credentials are held only in memory and excluded from retained evidence.
"""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from dotenv import load_dotenv
from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.llm import llm_client
from adaos.services.root.client import RootHttpClient, RootHttpError
from adaos.services.builder.prototype_mcp import _result
from adaos.services.contract_projections import project_context_units, publish_context_units
from adaos.services.artifact_pipeline.storage import atomic_write_json


def qualify(base_url, *, root_http, identity, expected):
    bootstrap = RootHttpClient(base_url=base_url, verify=root_http.verify, cert=root_http.cert,
        default_headers=root_http.default_headers)
    capabilities = ['development.read.descriptors']
    issued = _result(bootstrap.request('POST', '/v1/root/mcp/call', headers=identity, json={
        'tool_id': 'hub.issue_mcp_session', 'arguments': {'target_id': 'hub:'+identity['X-AdaOS-Subnet-Id'],
            'audience': 'root-context-qualification', 'capability_profile': 'ProfileOpsRead',
            'capabilities': capabilities, 'ttl_seconds': 600, 'note': 'public-context-read-only-qualification'}}))
    if set(issued['capabilities']) != set(capabilities): raise ValueError('Root widened the requested capability scope')
    scoped = RootHttpClient(base_url=base_url, verify=root_http.verify, cert=None,
        default_headers={'Authorization': 'Bearer '+issued['access_token']})
    def call(tool, arguments):
        return _result(scoped.request('POST', '/v1/root/mcp/call', json={'tool_id': tool, 'arguments': arguments}))
    description = call('development.describe_context_projection', {})
    if description['projection_digest'] != expected['projection_digest']: raise ValueError('Remote publication differs from Core projection')
    rows = []
    def read(**locator):
        started = time.perf_counter()
        first = call('development.get_descriptor_item', locator)
        second = call('development.get_descriptor_item', locator)
        if not second.get('_cache', {}).get('hit') or second['_cache']['scope'] != 'platform': raise ValueError('Root public cache did not hit')
        rows.append({'locator': locator, 'cache_hit': True, 'two_reads_wall_ms': round((time.perf_counter()-started)*1000)})
        return first
    publication = publish_context_units(expected, read=read)
    # Exercise the native MCP transport too, not only its REST convenience API.
    descriptor = scoped.request('GET', f"/v1/root/mcp/sessions/{issued['session_id']}/openai-tool",
        params={'server_label': 'context-qualification', 'require_approval': 'never'})
    from urllib.parse import urlsplit
    remote = urlsplit(descriptor['openai']['responses_tool']['server_url'])
    if remote.scheme != 'https' or remote.hostname not in {'mcp.api.inimatic.com', 'mcp.ru.api.inimatic.com',
            'api.inimatic.com', 'ru.api.inimatic.com'} or remote.path != '/v1/root/mcp':
        raise ValueError('Untrusted native MCP endpoint')
    native = RootHttpClient(base_url=f'{remote.scheme}://{remote.netloc}', verify=root_http.verify, cert=None,
        default_headers=scoped.default_headers)
    listed = native.request('POST', remote.path, headers={'Accept': 'application/json'},
        json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    tool = next(item for item in listed['result']['tools'] if item['name'].endswith('get_descriptor_item'))
    if 'expected_source_digest' not in tool['inputSchema']['properties']:
        raise ValueError('Native MCP schema cannot deliver source pins')
    result = native.request('POST', remote.path, headers={'Accept': 'application/json'}, json={
        'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': tool['name'],
            'arguments': expected['units'][0]['locator']}})
    if result.get('error') or result.get('result', {}).get('isError'):
        raise ValueError('Native MCP read failed')
    payload = result['result'].get('structuredContent') or json.loads(result['result']['content'][0]['text'])
    if _result(payload)['item_id'] != expected['units'][0]['locator']['item_id']:
        raise ValueError('Native MCP returned another locator')
    stale = {**expected['units'][0]['locator'], 'expected_source_digest': 'sha256:'+'0'*64}
    try:
        call('development.get_descriptor_item', stale)
    except RootHttpError as error:
        if error.status_code != 409: raise
    else: raise ValueError('Root accepted a stale source pin')
    anonymous = RootHttpClient(base_url=base_url, verify=root_http.verify, cert=None,
        default_headers={'Mcp-Session-Id': issued['session_id']})
    try:
        anonymous.request('POST', '/v1/root/mcp/call', json={'tool_id': 'development.describe_context_projection', 'arguments': {}})
    except RootHttpError as error:
        if error.status_code != 401: raise
    else: raise ValueError('Session ID unexpectedly acted as a credential')
    return {'root': base_url, 'status': 'passed', 'publication': publication, 'reads': rows,
        'stale_pin_rejected': True, 'session_id_only_rejected': True, 'lease_ttl_seconds': 600,
        'native_mcp_discovery_and_read': True,
        'model_calls': 0, 'usd': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'e2e/artifacts'): parser.error('Evidence belongs under e2e/artifacts')
    load_dotenv(); init_ctx(Settings.from_sources())
    http, cfg = llm_client._root_http_client()
    identity = llm_client._identity_headers(llm_client._current_ctx(), cfg)
    expected = project_context_units(sdk_symbols=['adaos.sdk.builder.semantic_ui.admit', 'adaos.sdk.resources.query'])
    results = [qualify(base, root_http=http, identity=identity, expected=expected) for base in
        ('https://api.inimatic.com', 'https://ru.api.inimatic.com')]
    atomic_write_json(args.output, {'status': 'passed', 'roots': results, 'model_calls': 0, 'usd': 0})
    print(json.dumps({'status': 'passed', 'roots': len(results), 'projection_digest': expected['projection_digest']}))


if __name__ == '__main__': main()
