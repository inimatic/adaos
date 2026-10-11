"""Credential-safe before/after checks for Root MCP authentication hardening.

No model calls; probes use only newly issued, public-guidance read-only leases.
Neither credentials, response bodies nor SSE/session identifiers are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.sdk.llm import llm_client
from adaos.services.builder import prototype_mcp as mcp
from adaos.services.root.client import RootHttpClient
from adaos.services.root_mcp.client import RootMcpClient, RootMcpClientConfig


def check(condition, label):
    if not condition:
        raise AssertionError(label)


def rpc(method="tools/list", request_id=1, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method,
            **({"params": params} if params is not None else {})}


def sse_data(lines, event):
    current = None
    for line in lines:
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: ") and current == event:
            return line[6:]
    raise AssertionError("SSE event missing")


def run_root(http, cfg, phase, result, untrusted_cert=None):
    headers = {"Accept": "application/json", "User-Agent": "AdaOS-root-mcp-auth-compat/1.0"}
    identity = llm_client._identity_headers(llm_client._current_ctx(), cfg)
    check(http.cert is not None, "Configured SDK mTLS certificate required")
    with patch.object(llm_client, "_root_http_client", return_value=(http, cfg)):
        lease = mcp.issue_protocol()
    descriptor = lease["tools"][0]
    authorized = {**headers, "Authorization": "Bearer " + descriptor["authorization"]}
    session_id = lease["session_id"]
    public_url = urlsplit(descriptor["server_url"])
    public = RootHttpClient(base_url=f"{public_url.scheme}://{public_url.netloc}",
                            verify=http.verify, cert=None)
    result.update(library_digest=lease["library_digest"], native_tools=lease["native_discovery"],
                  provider_url=descriptor["server_url"], checks=[])

    def record(label, response, expected):
        result["checks"].append({"check": label, "status": response.status_code, "expected": expected,
                                 "ok": response.status_code == expected})
        check(response.status_code == expected, label)
        return response

    if untrusted_cert:
        # A certificate from another CA is transport data, never AdaOS identity.
        unknown = RootHttpClient(base_url=public.base_url, verify=http.verify, cert=untrusted_cert)
        with unknown.session(timeout=15) as transport:
            record("untrusted_client_certificate_without_bearer", transport.client.post(public_url.path,
                   headers=headers, json=rpc()), 401)
            record("untrusted_client_certificate_with_valid_bearer", transport.client.post(public_url.path,
                   headers=authorized, json=rpc()), 200)

    with public.session(timeout=15) as transport, http.session(timeout=15) as sdk:
        client = transport.client
        received = record("native_bearer_tools_list", client.post(public_url.path, headers=authorized,
                                                                  json=rpc()), 200).json()
        check({tool["name"] for tool in received.get("result", {}).get("tools", [])} == set(mcp.OPENAI_TOOLS),
              "Exact read-only native tool discovery")
        received = record("native_initialize_with_session_header", client.post(public_url.path,
            headers={**authorized, "Mcp-Session-Id": session_id}, json=rpc("initialize")), 200)
        check(received.headers.get("Mcp-Session-Id") == session_id, "Session correlation header retained")
        read_args = {"fragment_id": "view_roles", "expected_digest": lease["library_digest"]}
        payload = record("native_bearer_fragment_read", client.post(public_url.path, headers=authorized,
            json=rpc("tools/call", params={"name": mcp.OPENAI_TOOLS[1], "arguments": read_args})), 200).json()
        check(not payload.get("error"), "Native fragment RPC successful")
        content = json.loads(payload["result"]["content"][0]["text"])
        fragment = content.get("result", content)["fragment"]
        check(fragment == next(part for part in mcp.library_snapshot()["fragments"] if part["id"] == "view_roles"),
              "Native fragment matches pinned SDK content")
        result["exact_fragment_verified"] = True

        scoped = RootHttpClient(base_url=http.base_url, verify=http.verify, cert=None,
                                default_headers=authorized)
        configured = RootMcpClientConfig(root_url=http.base_url, access_token=descriptor["authorization"])
        check(configured.headers()["Authorization"] == authorized["Authorization"], "SDK bearer configuration")
        compatibility_client = RootMcpClient(configured, http=scoped)
        check(mcp._result(compatibility_client.call(mcp.READ_TOOL, arguments=read_args))["fragment"] == fragment,
              "RootMcpClient read compatibility")
        result["root_mcp_client_bearer_compatible"] = True
        read_body = {"tool_id": mcp.READ_TOOL, "arguments": read_args}
        received = record("sdk_mtls_without_bearer", sdk.client.post("/v1/root/mcp/call",
                          headers={**headers, **identity}, json=read_body), 200).json()
        check(mcp._result(received)["fragment"] == fragment, "SDK mTLS fragment read")
        received = record("public_native_mtls_without_bearer", sdk.client.post(descriptor["server_url"],
            headers={**headers, **identity}, json=rpc("tools/call", params={"name": mcp.READ_TOOL,
                "arguments": read_args})), 401 if phase == "before" else 200)
        if phase == "after":
            check(not received.json().get("error"), "Public native mTLS RPC read")

        for tool in ("foundation", "hub.issue_mcp_session"):
            record("read_only_denies_" + tool, client.post(http.base_url + "/v1/root/mcp/call",
                headers=authorized, json={"tool_id": tool, "arguments": {}}), 403)
        record("native_without_credentials", client.post(public_url.path, headers=headers, json=rpc()), 401)
        record("untrusted_proxy_headers_without_certificate", client.post(public_url.path,
            headers={**headers, "X-SSL-Client-Verify": "SUCCESS", "X-Client-Subject": "/CN=subnet:auth-probe"},
            json=rpc()), 200 if phase == "before" else 401)
        endpoints = (("native", "POST", descriptor["server_url"], rpc()),
                     ("rest", "POST", http.base_url + "/v1/root/mcp/call", read_body),
                     ("descriptor", "GET", http.base_url + f"/v1/root/mcp/sessions/{session_id}/openai-tool", None))
        for mode, extra in (("session_id_only", {}), ("invalid_bearer_plus_session_id", {"Authorization": "Bearer invalid-auth-probe"})):
            for label, method, endpoint, body in endpoints:
                response = record(mode + "_" + label, client.request(method, endpoint,
                    headers={**headers, **extra, "Mcp-Session-Id": session_id}, **({"json": body} if body else {})),
                    200 if phase == "before" else 401)
                if label == "descriptor":
                    result.setdefault("descriptor_token_recovery", []).append({"mode": mode,
                        "token_returned": bool(response.json().get("openai", {}).get("responses_tool", {}).get("authorization"))})

        # A second valid lease must not be allowed to reuse the first SSE stream.
        subnet = identity.get("X-AdaOS-Subnet-Id") or getattr(cfg, "subnet_id", "")
        second = mcp._result(sdk.request("POST", "/v1/root/mcp/call", headers=identity,
            json={"tool_id": "hub.issue_mcp_session", "arguments": {"target_id": "hub:" + subnet,
                "audience": "openai-builder-prototype", "capability_profile": "BuilderPrototypeRead",
                "capabilities": [mcp.CAPABILITY], "ttl_seconds": 300, "note": "auth-compat-read-only"}}))
        check(second["access_token"] != descriptor["authorization"], "Distinct SSE identity required")
        with client.stream("GET", "/v1/root/mcp/sse", headers={**authorized, "Accept": "text/event-stream"}) as stream:
            record("legacy_sse_connect_bearer", stream, 200)
            lines = stream.iter_lines()
            endpoint = sse_data(lines, "endpoint")
            parsed = urlsplit(endpoint)
            check(parsed.scheme == "https" and parsed.hostname in {urlsplit(http.base_url).hostname, public_url.hostname}
                  and parsed.port in {None, 443} and parsed.path.startswith("/v1/root/mcp/sse/messages/mcp_sse_")
                  and not (parsed.query or parsed.fragment or parsed.username or parsed.password), "Trusted SSE endpoint")
            cases = (("legacy_sse_without_credentials", headers, 401),
                     ("legacy_sse_session_header_only", {**headers, "Mcp-Session-Id": session_id}, 401),
                     ("legacy_sse_invalid_bearer", {**headers, "Authorization": "Bearer invalid-auth-probe"}, 401),
                     ("legacy_sse_different_valid_bearer", {**headers, "Authorization": "Bearer " + second["access_token"]}, 403),
                     ("legacy_sse_same_bearer", authorized, 202))
            for index, (label, credentials, status) in enumerate(cases):
                record(label, client.post(endpoint, headers=credentials, json=rpc(request_id=100 + index)),
                       202 if phase == "before" else status)
            while True:
                message = json.loads(sse_data(lines, "message"))
                if message.get("id") == 104:
                    check({tool["name"] for tool in message.get("result", {}).get("tools", [])} == set(mcp.OPENAI_TOOLS),
                          "Legacy SSE exact tool discovery")
                    break
        result["legacy_sse_bearer_compatible"] = True
        with sdk.client.stream("GET", "/v1/root/mcp/sse",
            headers={**headers, **identity, "Accept": "text/event-stream"}) as stream:
            record("legacy_sse_connect_mtls", stream, 200)
            lines = stream.iter_lines()
            endpoint = sse_data(lines, "endpoint")
            parsed = urlsplit(endpoint)
            check(parsed.scheme == "https" and parsed.hostname == urlsplit(http.base_url).hostname
                  and parsed.path.startswith("/v1/root/mcp/sse/messages/mcp_sse_")
                  and parsed.port in {None, 443} and not (parsed.query or parsed.fragment or parsed.username or parsed.password),
                  "Trusted mTLS SSE endpoint")
            record("legacy_sse_message_mtls", sdk.client.post(endpoint, headers={**headers, **identity},
                json=rpc("tools/call", request_id=201, params={"name": mcp.READ_TOOL, "arguments": read_args})), 202)
            message = json.loads(sse_data(lines, "message"))
            check(message.get("id") == 201 and not message.get("error"), "Legacy mTLS fragment RPC")
            content = json.loads(message["result"]["content"][0]["text"])
            check(content.get("result", content)["fragment"] == fragment, "Legacy mTLS exact fragment")
        result["legacy_sse_mtls_compatible"] = True
    result["ok"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root-url", choices=("https://ru.api.inimatic.com", "https://api.inimatic.com"))
    parser.add_argument("--untrusted-cert", type=Path)
    parser.add_argument("--untrusted-key", type=Path)
    args = parser.parse_args()
    if bool(args.untrusted_cert) != bool(args.untrusted_key):
        parser.error("Supply both untrusted certificate and key")
    load_dotenv()
    logging.disable(logging.CRITICAL)
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("DEV only")
    output = args.output.resolve()
    if not output.is_relative_to(Path.cwd() / "e2e/artifacts/root"):
        parser.error("Requires owned Root E2E output")
    output.mkdir(parents=True, exist_ok=True)
    suffix = "-" + urlsplit(args.root_url).hostname if args.root_url else ""
    receipt_path = output / (args.phase + suffix + ".json")
    if receipt_path.exists():
        parser.error("Refusing to overwrite an existing receipt")
    from adaos.apps.cli.app import Settings, init_ctx
    init_ctx(Settings.from_sources())
    primary, cfg = llm_client._root_http_client()
    receipt = {"schema": "adaos.e2e.root_mcp_auth_compat.v1", "phase": args.phase,
               "started_at": datetime.now(timezone.utc).isoformat(), "roots": [], "ok": False,
               "new_llm_calls": 0, "credentials_retained": False,
               "scope": "Public SDK guidance only; short-lived scoped leases"}
    try:
        with patch.object(llm_client, "submit_response_job", side_effect=AssertionError("No model submission allowed")):
            for index, url in enumerate(llm_client._root_llm_base_urls(primary)):
                if args.root_url and url != args.root_url:
                    continue
                http = llm_client._root_http_for_base(primary, primary.base_url, url, index)
                result = {"root_url": url, "ok": False}
                receipt["roots"].append(result)
                run_root(http, cfg, args.phase, result,
                         (str(args.untrusted_cert), str(args.untrusted_key)) if args.untrusted_cert else None)
                _write_json(receipt_path, receipt)
                print(json.dumps({"phase": args.phase, "root_url": url, "ok": True,
                                  "checks": len(result["checks"])}), flush=True)
        check(len(receipt["roots"]) == (1 if args.root_url else 2), "Requested Root routes required")
        receipt["ok"] = True
    except Exception as error:
        # Do not retain arbitrary HTTP exception strings: they can contain a URL/session ID.
        receipt["error_type"] = type(error).__name__
        # Numeric HTTP status is safe diagnostic evidence; exception strings
        # and server payloads can contain URLs, bearer tokens or session ids.
        if isinstance(error, RootHttpError):
            receipt['http_status'] = error.status_code
        if isinstance(error, AssertionError):
            receipt["failed_check"] = str(error)
        print(json.dumps({"phase": args.phase, "ok": False, "error_type": type(error).__name__,
                          "http_status": receipt.get('http_status'),
                          "failed_check": receipt.get("failed_check")}), flush=True)
    finally:
        receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(receipt_path, receipt)
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
