"""Native Responses remote-MCP smoke; deliberately instructed, not voluntary use."""
import argparse
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_mcp_eval import mcp_trace
from adaos.services.builder import prototype_mcp as mcp
from adaos.services.builder_context import digest

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--model", choices=("gpt-5", "gpt-6-astra"), required=True)
parser.add_argument("--resume", action="store_true", help="Collect the already submitted paid smoke without resubmitting")
parser.add_argument('--exercise-error-recovery', action='store_true', help='Instruct an unknown-fragment call followed by a valid read; keep separate from voluntary calls')
parser.add_argument('--budget-report', type=Path, help='Completed series report sharing the user-approved request ceiling')
args = parser.parse_args()
load_dotenv()
logging.disable(logging.INFO)
if os.getenv("ENV_TYPE") != "dev":
    parser.error("DEV only")
output = args.output.resolve()
if not output.is_relative_to(Path.cwd() / "e2e/artifacts/builder") or output.exists() != args.resume:
    parser.error("Requires a new owned E2E output")
output.mkdir(parents=True, exist_ok=args.resume)
from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.llm import llm_client
from adaos.services.root.client import RootHttpClient, RootHttpError
init_ctx(Settings.from_sources())


def finish(submitted):
    terminal = submitted if submitted.get("status") == "succeeded" else llm_client.wait_response_job(
        submitted["job_id"], timeout_s=1200, base_url=submitted.get("_client", {}).get("base_url"))
    _write_json(output / "terminal.json", mcp.redact_credentials(terminal))
    trace = mcp_trace(terminal)
    trace["instructed_smoke_not_voluntary"] = True
    try:
        final = json.loads(terminal.get("output_text") or "")
    except ValueError:
        final = {}
    trace["final_fragment_and_digest_verified"] = final == {"fragment_id": "view_roles", "library_digest": mcp.library_snapshot()["library_digest"]}
    trace["ok"] = (terminal.get("status") == "succeeded" and trace["errors"] == 0
        and {call["name"] for call in trace["calls"]} == set(mcp.OPENAI_TOOLS)
        and trace["final_fragment_and_digest_verified"])
    if args.exercise_error_recovery:
        calls = trace['calls']
        def expected_execution_error(call):
            error = call.get('error') or {}
            codes = []
            for content in error.get('content', []):
                if content.get('type') != 'text':
                    continue
                try:
                    codes.append(json.loads(content['text']).get('error'))
                except (ValueError, TypeError):
                    continue
            # Responses represents an MCP isError result as a failed call with
            # mcp_tool_execution_error, not as a successful output envelope.
            return (error.get('type') == 'mcp_tool_execution_error'
                and codes == ['prototype_guidance_fragment_not_found'])
        expected_errors = [call for call in calls if call['name'] == mcp.OPENAI_TOOLS[1]
            and call['arguments'].get('fragment_ids') == ['view_roles', 'not_a_fragment']
            and (expected_execution_error(call)
                or (call['tool_result_error'] and not call['error'] and call['status'] == 'completed'))]
        recovered = [call for call in calls if call['name'] == mcp.OPENAI_TOOLS[1]
            and call['fragment_id'] == 'view_roles' and not call['tool_result_error'] and not call['error']]
        trace['expected_tool_errors'] = len(expected_errors)
        trace['unexpected_errors'] = trace['errors'] - len(expected_errors)
        trace['expected_error_classification'] = 'intentional known-id miss is an MCP tool execution error; protocol/session/auth errors remain failures'
        trace['ok'] = (terminal.get('status') == 'succeeded' and len(expected_errors) == 1
            and len(recovered) == 1 and calls.index(expected_errors[0]) < calls.index(recovered[0])
            and trace['unexpected_errors'] == 0 and trace['final_fragment_and_digest_verified'])
    _write_json(output / "result.json", trace)
    print(json.dumps({"stage": "smoke_complete", "model": args.model, "ok": trace["ok"], "native_mcp_calls": trace["call_count"], "errors": trace["errors"]}), flush=True)
    raise SystemExit(0 if trace["ok"] else 1)


if args.resume:
    finish(json.loads((output / "submission.json").read_text(encoding="utf-8")))
os.environ["ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED"] = "1"
primary, cfg = llm_client._root_http_client()
proofs = []
for index, url in enumerate(llm_client._root_llm_base_urls(primary)):
    http = llm_client._root_http_for_base(primary, primary.base_url, url, index)
    with patch.object(llm_client, "_root_http_client", return_value=(http, cfg)):
        lease = mcp.issue_protocol()
    tool = lease["tools"][0]
    scoped = RootHttpClient(base_url=url, verify=http.verify, default_headers={"Authorization": "Bearer " + tool["authorization"], "Accept": "application/json"})
    received = scoped.request("POST", "/v1/root/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": mcp.OPENAI_TOOLS[1], "arguments": {"fragment_id": "view_roles", "expected_digest": lease["library_digest"]}}}, timeout=15)
    assert not received.get("error"), "Native protocol read failed"
    content = json.loads(received["result"]["content"][0]["text"])
    result = content.get("result", content)
    assert result["library_digest"] == lease["library_digest"] and result["fragment"]["id"] == "view_roles"
    expected = next(part for part in mcp.library_snapshot()["fragments"] if part["id"] == "view_roles")
    assert result["fragment"] == expected
    if args.exercise_error_recovery:
        rejected = scoped.request('POST', '/v1/root/mcp', json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': mcp.OPENAI_TOOLS[1], 'arguments': {'fragment_ids': ['view_roles', 'not_a_fragment'],
                'expected_digest': lease['library_digest']}}}, timeout=15)
        assert not rejected.get('error') and rejected['result']['isError']
        retry = scoped.request('POST', '/v1/root/mcp', json={'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
            'params': {'name': mcp.OPENAI_TOOLS[1], 'arguments': {'fragment_id': 'view_roles',
                'expected_digest': lease['library_digest']}}}, timeout=15)
        assert not retry.get('error') and not retry['result']['isError']
    negative = []
    for tool_name in ("foundation", "hub.issue_mcp_session"):
        try:
            scoped.request("POST", "/v1/root/mcp/call", json={"tool_id": tool_name, "arguments": {}}, timeout=15)
        except RootHttpError as exc:
            assert exc.status_code == 403
            negative.append({"tool": tool_name, "status": 403})
        else:
            raise AssertionError("Read-only scope unexpectedly broadened")
    proofs.append({"root_url": url, **{key: value for key, value in lease.items() if key not in {"tools", "session_id"}},
        "exact_fragment_verified": True, "negative_capability_checks": negative})
_write_json(output / "root-mcp-preflight.json", proofs)
options = mcp.generation_options()
messages = [{"role": "system", "content": "This is an instructed MCP connectivity test, not an application task. Use the supplied native tools, never imitate a tool result."},
    {"role": "user", "content": json.dumps({"instruction": "First search Prototype guidance for editor. Then read fragment view_roles. Return only JSON with fragment_id and the library_digest returned by the tool.", "expected_digest": mcp.library_snapshot()["library_digest"]})}]
options.update(model=args.model, reasoning={"effort": "high"}, max_tokens=16000,
    text={"verbosity": "low"}, profile_scope="development", provider_timeout_s=900,
    request_id="prototype-mcp-smoke-" + digest(str(output))[7:23])
if args.exercise_error_recovery:
    if not args.budget_report:
        parser.error('An explicit shared budget report is required before a paid recovery smoke')
    budget_path = args.budget_report.resolve()
    budget = json.loads(budget_path.read_text(encoding='utf-8'))
    suite = budget_path.parent / 'mcp-error-recovery'
    assert output.parent == suite and budget['complete']
    prior_smokes = len(list(suite.glob('*/request.json')))
    series_attempts = budget['combined_attempted_requests'] - len(budget.get('instructed_mcp_error_recovery', []))
    assert series_attempts + prior_smokes + 1 <= 32, 'User-approved request ceiling exceeded'
    messages[1]['content'] = json.dumps({'instruction': 'First call builder_read_prototype_guidance with fragment_ids ["view_roles", "not_a_fragment"] and expected_digest. This deliberate invalid-input test must return a tool error. Then call the SAME tool with fragment_id "view_roles" and expected_digest to verify recovery. Do not search or retry the invalid call. Return only JSON with fragment_id and the library_digest from the successful read.',
        'expected_digest': mcp.library_snapshot()['library_digest']})
    options['max_tokens'] = 4000
    _write_json(output / 'shared-budget.json', {'prior_attempted_requests': series_attempts,
        'prior_recovery_smokes': prior_smokes, 'request_ceiling': 32, 'instructed_not_voluntary': True})
_write_json(output / "request.json", mcp.redact_credentials({"messages": messages, "options": options}))
submitted = llm_client.submit_response_job(messages, **options)
_write_json(output / "submission.json", mcp.redact_credentials(submitted))
print(json.dumps({"stage": "smoke_submitted", "model": args.model, "job_id": submitted.get("job_id")}), flush=True)
finish(submitted)
