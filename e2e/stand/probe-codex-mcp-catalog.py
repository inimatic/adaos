"""Qualify real CLI catalog admission with synthetic loopback MCP/provider endpoints.

No provider inference, installed application, Root credential or task lease is used.
The fake provider deliberately stops at the first request, after catalog capture.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adaos.services.skill_factory_worker import SubprocessCodexExecutor


def run_case(executable: str, home: Path, output: Path, *, delay: float, legacy: bool,
             startup_timeout: int = 10, required: bool = False) -> dict:
    events: list[dict] = []
    started = time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status: int, payload: dict):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionError):
                pass  # Legacy startup can abandon a still-pending catalog request.

        def do_GET(self):
            self.reply(405, {})

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size > 2 * 1024 * 1024:
                self.reply(413, {})
                return
            payload = json.loads(self.rfile.read(size))
            if self.path == "/v1/responses":
                tools = payload.get("tools", [])
                events.append({"phase": "model_request", "seconds": time.monotonic() - started,
                    "server_advertised": "adaos_task_root" in json.dumps(tools),
                    "tool_types": sorted({str(tool.get("type")) for tool in tools})})
                self.reply(400, {"error": {"type": "invalid_request_error",
                    "message": "Offline diagnostic stops before inference"}})
                return
            if self.path != "/mcp":
                self.reply(404, {})
                return
            method = payload.get("method")
            events.append({"phase": method, "seconds": time.monotonic() - started})
            if method == "notifications/initialized":
                self.reply(202, {})
                return
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "adaos-task-fixture", "version": "1"}}
            elif method == "tools/list":
                time.sleep(delay)
                result = {"tools": [{"name": "foundation", "description": "Synthetic read-only foundation",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
                events.append({"phase": "tools_returned", "seconds": time.monotonic() - started})
            else:
                self.reply(400, {"error": "Unexpected diagnostic method"})
                return
            self.reply(200, {"jsonrpc": "2.0", "id": payload.get("id"), "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    profile = {"server_name": "adaos_task_root", "url": base + "/mcp", "enabled": True,
        "required": required, "enabled_tools": ["foundation"], "startup_timeout_sec": startup_timeout}
    command = [executable, "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only", "-C", str(home),
        "--model", "gpt-5.5"]
    command.extend(SubprocessCodexExecutor._root_mcp_config_args(profile))
    overrides = {"model_provider": "probe", "model_providers.probe.name": "Offline capture",
        "model_providers.probe.base_url": base + "/v1", "model_providers.probe.wire_api": "responses",
        "model_providers.probe.requires_openai_auth": False, "approval_policy": "never"}
    if legacy:
        overrides["mcp_optional_startup_grace_ms"] = 1000
    for key, value in overrides.items():
        command.extend(["-c", f"{key}={json.dumps(value)}"])
    command.append("Reply only with ok. This is an offline catalog diagnostic.")
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE"}}
    env["CODEX_HOME"] = str(home)
    try:
        result = subprocess.run(command, cwd=home, env=env, input="", capture_output=True,
            text=True, encoding="utf-8", timeout=50)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    output.mkdir()
    (output / "cli.jsonl").write_text(result.stdout, encoding="utf-8")
    (output / "cli.stderr.log").write_text(result.stderr, encoding="utf-8")
    requests = [item for item in events if item["phase"] == "model_request"]
    expected_advertised = delay < startup_timeout and (not legacy or delay < 1)
    expected_requests = 0 if required and not expected_advertised else 1
    return {"case": output.name, "delay_seconds": delay, "legacy_grace": legacy,
        "startup_timeout_seconds": startup_timeout, "required": required,
        "exit_code": result.returncode, "events": events,
        "passed": result.returncode == 1 and len(requests) == expected_requests
            and (not requests or requests[0]["server_advertised"] == expected_advertised)
            and any(item["phase"] == "tools_returned" for item in events)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    executable = shutil.which("codex")
    if not executable:
        parser.error("Installed Codex CLI required")
    args.output.mkdir(parents=True, exist_ok=False)
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    scratch = Path(__file__).resolve().parents[2] / ".tmp"
    scratch.mkdir(exist_ok=True)
    cases = []
    for name, delay, legacy, timeout, required in [
        ("fast", 0, False, 10, False),
        ("delayed-legacy", 3, True, 10, False),
        ("delayed-admitted", 3, False, 10, False),
        ("optional-deadline", 3, False, 1, False),
        ("required-deadline", 3, False, 1, True),
    ]:
        with tempfile.TemporaryDirectory(prefix="mcp-catalog-", dir=scratch) as directory:
            case = run_case(executable, Path(directory), args.output / name, delay=delay, legacy=legacy,
                startup_timeout=timeout, required=required)
        cases.append(case)
        print(json.dumps(case, ensure_ascii=False), flush=True)
    report = {"schema": "adaos.e2e.codex_catalog_probe.v1", "cli_version": version,
        "scope": "Synthetic local transport only; no inference or live Root readiness claim",
        "passed": all(case["passed"] for case in cases), "cases": cases}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
