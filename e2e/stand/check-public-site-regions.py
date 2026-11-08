"""Check a public site facade from external Globalping probes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests


ROOT = Path(__file__).resolve().parents[2]
API = "https://api.globalping.io/v1/measurements"
USER_AGENT = "AdaOS-Public-Site-Qualification/1.0"
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--locations", default="Russia,Germany,United States",
        help="Comma-separated Globalping location selectors.",
    )
    parser.add_argument("--expected-status", type=int, default=200)
    parser.add_argument("--expected-deployment-digest")
    parser.add_argument("--expected-primary-zone")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    target = urlsplit(args.url)
    if target.scheme not in {"http", "https"} or not target.hostname:
        parser.error("--url must be an absolute HTTP(S) URL")
    locations = [item.strip() for item in args.locations.split(",") if item.strip()]
    if not locations:
        parser.error("--locations must not be empty")
    if args.expected_deployment_digest and not DIGEST.fullmatch(
        args.expected_deployment_digest
    ):
        parser.error("--expected-deployment-digest must be a sha256 digest")
    request_path = target.path or "/"
    payload = {
        "type": "http",
        "target": target.hostname,
        "locations": [{"magic": item, "limit": 1} for item in locations],
        "measurementOptions": {
            "request": {
                "method": "GET",
                "path": request_path,
                **({"query": target.query} if target.query else {}),
            },
            "protocol": "HTTPS" if target.scheme == "https" else "HTTP",
            **({"port": target.port} if target.port else {}),
        },
    }
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    created = session.post(API, json=payload, timeout=30)
    created.raise_for_status()
    measurement_id = str(created.json()["id"])
    deadline = time.monotonic() + args.timeout
    while True:
        response = session.get(f"{API}/{measurement_id}", timeout=30)
        response.raise_for_status()
        measurement = response.json()
        if measurement.get("status") != "in-progress":
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"regional measurement {measurement_id} timed out")
        time.sleep(0.75)

    checks = []
    ok = measurement.get("status") == "finished"
    for entry in measurement.get("results") or []:
        probe = entry.get("probe") or {}
        result = entry.get("result") or {}
        tls = result.get("tls") or {}
        status_code = result.get("statusCode")
        routing = None
        routing_ok = True
        if args.expected_deployment_digest or args.expected_primary_zone:
            try:
                descriptor = json.loads(result.get("rawBody") or "")
                zones = descriptor.get("zones") or []
                routing = {
                    "schema": descriptor.get("schema"),
                    "generation": descriptor.get("generation"),
                    "deployment_digest": descriptor.get("deployment_digest"),
                    "descriptor_digest": descriptor.get("descriptor_digest"),
                    "zones": [item.get("zone_id") for item in zones],
                }
                routing_ok = (
                    descriptor.get("schema")
                    == "adaos.public_site_routing_descriptor.v1"
                    and (
                        not args.expected_deployment_digest
                        or descriptor.get("deployment_digest")
                        == args.expected_deployment_digest
                    )
                    and (
                        not args.expected_primary_zone
                        or bool(zones)
                        and zones[0].get("zone_id") == args.expected_primary_zone
                    )
                )
            except (AttributeError, json.JSONDecodeError, TypeError):
                routing_ok = False
        check_ok = (
            result.get("status") == "finished"
            and status_code == args.expected_status
            and (target.scheme != "https" or tls.get("authorized") is True)
            and routing_ok
        )
        ok = ok and check_ok
        checks.append(
            {
                "ok": check_ok,
                "country": probe.get("country"),
                "city": probe.get("city"),
                "network": probe.get("network"),
                "asn": probe.get("asn"),
                "resolved_address": result.get("resolvedAddress"),
                "status_code": status_code,
                "timings_ms": result.get("timings"),
                "routing": routing,
                "tls": {
                    "authorized": tls.get("authorized"),
                    "protocol": tls.get("protocol"),
                    "subject": tls.get("subject"),
                    "expires_at": tls.get("expiresAt"),
                },
            }
        )
    if len(checks) != len(locations):
        ok = False
    receipt = {
        "schema": "adaos.e2e.public_site_regions.v1",
        "ok": ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "provider": "globalping",
        "measurement_id": measurement_id,
        "requested_locations": locations,
        "checks": checks,
    }
    output = args.output.resolve()
    artifacts = (ROOT / "e2e/artifacts").resolve()
    if not output.is_relative_to(artifacts):
        parser.error("--output must be below e2e/artifacts")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
