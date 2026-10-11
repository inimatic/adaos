"""Read bounded Root MCP transport evidence; never collect credentials."""
import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from adaos.e2e.builder import _write_json
from adaos.services.builder.prototype_mcp import _result, library_snapshot, TOOLS

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / "e2e/artifacts/builder"):
    parser.error("Owned E2E artifacts only")
load_dotenv()
logging.disable(logging.INFO)
from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.llm import llm_client
init_ctx(Settings.from_sources())
primary, config = llm_client._root_http_client()
headers = llm_client._identity_headers(llm_client._current_ctx(), config)
digest = library_snapshot()["library_digest"]
roots = []
for index, url in enumerate(llm_client._root_llm_base_urls(primary)):
    http = llm_client._root_http_for_base(primary, primary.base_url, url, index)
    events = _result(http.request("POST", "/v1/root/mcp/call", headers=headers,
        json={"tool_id": "hub.get_activity_log", "arguments": {"limit": 200}}, timeout=20)).get("events", [])
    transports, executions = [], []
    for event in events:
        meta = event.get("meta") or {}
        if event.get("kind") == "transport" and meta.get("audience") == "openai-builder-prototype":
            transports.append({"created_at": event.get("created_at"), **{key: meta.get(key) for key in (
                "phase", "http_method", "host", "path", "user_agent", "has_authorization", "authorization_scheme",
                "has_mcp_session_id", "auth_method", "json_rpc_method", "json_rpc_response_status", "json_rpc_has_error")}})
        elif event.get("tool_id") in TOOLS and meta.get("library_digest") == digest:
            executions.append({"created_at": event.get("created_at"), "tool": event.get("tool_id"),
                "status": event.get("status"), "auth_method": event.get("auth_method"),
                "fragment_id": meta.get("fragment_id"), "query": meta.get("query"), "library_digest": digest})
    roots.append({"root_url": url, "bounded_events_examined": len(events), "transport_events": transports,
        "guidance_executions": executions, "transport_events_without_authorization": sum(not row["has_authorization"] for row in transports)})
result = {"schema": "adaos.e2e.prototype_mcp_transport_audit.v1", "roots": roots,
    "credentials_retained": False, "library_digest": digest, "new_llm_calls": 0,
    "limitations": ["Root keeps a bounded rolling audit window; this is not an exhaustive call count",
        "Transport audit is audience-scoped, not provider-job correlated; smokes/host preflight can appear",
        "Provider terminal output remains the authority for voluntary model MCP counts"]}
_write_json(run / "root-mcp-transport-audit.json", result)
print(json.dumps({"roots": [{"url": root["root_url"], "transport_events": len(root["transport_events"]),
    "guidance_executions": len(root["guidance_executions"]),
    "without_authorization": root["transport_events_without_authorization"]} for root in roots]}), flush=True)
