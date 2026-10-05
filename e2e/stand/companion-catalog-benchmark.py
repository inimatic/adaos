"""Read-only cold/warm Companion MCP benchmark against an explicitly chosen node.

PYTHONPATH=src python e2e/stand/companion-catalog-benchmark.py --api http://127.0.0.1:8788 --webspace desktop
Credentials use the normal local control resolver and are never printed.
"""
import argparse
import json
import statistics
import time
from pathlib import Path

import requests
from adaos.apps.cli.active_control import resolve_control_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--webspace", required=True)
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()
    client = requests.Session()
    client.trust_env = False
    client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=args.api)
    rows = []
    failure = None
    for name in ("capabilities.search", "companion.context.read"):
        for iteration in range(max(2, args.samples)):
            parameters = {"webspace_id": args.webspace}
            if name == "capabilities.search":
                parameters.update(query="version", kind=None, limit=5, offset=0)
            started = time.perf_counter()
            try:
                response = client.post(args.api.rstrip("/")+"/api/admin/root_mcp/call", json={
                    "tool_id": name, "arguments": parameters, "capability_profile": "CompanionOperator",
                    "actor": "skill:conversation_companions", "dry_run": True}, timeout=45)
                response.raise_for_status()
                envelope = response.json()["response"]
                if not envelope.get("ok"):
                    raise RuntimeError(envelope.get("error"))
                result = envelope["result"]
            except Exception as exc:
                failure = {"tool": name, "iteration": iteration, "error": str(exc),
                           "http_ms": (time.perf_counter()-started)*1000}
                break
            rows.append({"tool": name, "iteration": iteration, "http_ms": (time.perf_counter()-started)*1000,
                **{key:result.get(key) for key in ("duration_ms", "cache_hit", "catalog_digest", "catalog_total")}})
        if failure:
            break
    summaries = []
    for name in ("capabilities.search", "companion.context.read"):
        warm = [r for r in rows if r["tool"] == name and r["cache_hit"]]
        for key in ("duration_ms", "http_ms"):
            values = sorted(r[key] for r in warm)
            summaries.append({"tool": name, "metric": key, "samples": len(values),
                "p50": statistics.median(values) if values else None,
                "p95": values[max(0, int(len(values)*.95+.999)-1)] if values else None})
    report = {"api": args.api, "webspace": args.webspace, "observations": rows, "warm": summaries, "error": failure}
    output = Path(__file__).resolve().parents[1] / "artifacts/companion-learning-lab"
    output.mkdir(parents=True, exist_ok=True)
    (output / "catalog-benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
