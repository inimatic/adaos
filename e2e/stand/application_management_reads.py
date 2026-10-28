"""Bounded, redacted local HTTP acceptance for Application management reads.

Does not navigate, select releases, invoke inference, or change permissions.
Setup's existing readiness reconciliation is exercised through its public read
surface. Each run records first and repeated reads separately; it never deletes
indexes or warms them secretly. Run before/after a controlled runtime restart.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import uuid

import requests
from dotenv import load_dotenv

from adaos.apps.cli.active_control import resolve_control_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--application-id", default="adaos_builder")
    parser.add_argument("--base", default="http://127.0.0.1:8777")
    parser.add_argument("--rounds", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    token = resolve_control_token(base_url=options.base)
    tools = ["applications.access.show", "applications.setup.show", "applications.list_placements"]

    def call(tool, sample):
        session = requests.Session()
        session.trust_env = False
        session.headers["X-AdaOS-Token"] = token
        started = time.perf_counter()
        response = session.post(options.base + "/api/admin/root_mcp/call", timeout=90, json={
            "tool_id": tool, "arguments": {"application_id": options.application_id, "webspace_id": "desktop"},
            "request_id": "management-read-acceptance:" + uuid.uuid4().hex, "dry_run": True,
        })
        payload = response.json()
        envelope = payload.get("response") or {}
        result = envelope.get("result") or {}
        return {"sample": sample, "tool": tool, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "http_status": response.status_code, "ok": payload.get("ok"), "status": envelope.get("status"),
                "error_code": (envelope.get("error") or {}).get("code"), "result_keys": sorted(result),
                "setup_available": (result.get("setup") or {}).get("available")}

    rows = []
    for index in range(options.rounds):
        for tool in tools:
            row = call(tool, f"sequential-{index+1}")
            rows.append(row)
            print(json.dumps(row), flush=True)
    if options.parallel:
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows.extend(pool.map(lambda tool: call(tool, "parallel-three-panels"), tools))
    report = {"schema": "adaos.application.management_read_acceptance.v1", "phase": options.phase,
              "at_msk": datetime.now(timezone(timedelta(hours=3))).isoformat(),
              "application_id": options.application_id, "calls": rows,
              "passed": all(x["http_status"] == 200 and x["ok"] is True for x in rows)}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"phase": options.phase, "passed": report["passed"], "calls": len(rows)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
