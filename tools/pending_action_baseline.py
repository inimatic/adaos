"""Build redacted, content-addressed Pending Actions baseline evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from adaos.services.pending_action_inventory import build_pending_action_baseline  # noqa: E402


def _object(path: Path, *, name: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def _array(path: Path, *, name: str) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{name} must contain a JSON array of objects")
    return [dict(item) for item in value]


def _artifact(path: Path, role: str) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "role": role,
        "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "bytes": len(raw),
        "redaction": "content_and_path_not_embedded",
    }


def _artifact_bytes(raw: bytes, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "bytes": len(raw),
        "redaction": "content_and_source_url_not_embedded",
    }


def _snapshot_from_url(
    url: str,
    *,
    token_env: str,
    token_header: str,
    timeout_seconds: float = 30.0,
    max_bytes: int = 2 * 1024 * 1024,
) -> tuple[dict[str, Any], bytes]:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("snapshot URL must use http or https")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("plain HTTP snapshot capture is limited to loopback")
    headers = {"Accept": "application/json"}
    token = str(os.environ.get(str(token_env or ""), "")).strip()
    if token:
        headers[str(token_header or "X-AdaOS-Token").strip()] = token
    request = Request(parsed.geturl(), headers=headers, method="GET")
    with urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:  # noqa: S310
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"snapshot response exceeds {max_bytes} bytes")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot response must contain a JSON object")
    snapshot = payload.get("value", payload)
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot response value must contain a JSON object")
    return dict(snapshot), raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    snapshot_source = parser.add_mutually_exclusive_group(required=True)
    snapshot_source.add_argument("--snapshot", type=Path)
    snapshot_source.add_argument("--snapshot-url")
    parser.add_argument("--token-env", default="ADAOS_CONTROL_TOKEN")
    parser.add_argument("--token-header", default="X-AdaOS-Token")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--revisions", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--sdk-discovery", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--failures", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "e2e" / "artifacts" / "pending-actions" / "baseline.json",
    )
    args = parser.parse_args(argv)

    inputs = {
        "revisions": args.revisions.resolve(),
        "topology": args.topology.resolve(),
        "sdk_discovery": args.sdk_discovery.resolve(),
        "sample": args.sample.resolve(),
    }
    if args.snapshot is not None:
        inputs["snapshot"] = args.snapshot.resolve()
    if args.failures is not None:
        inputs["failures"] = args.failures.resolve()
    output = args.output.resolve()
    allowed_roots = (
        (REPOSITORY_ROOT / ".tmp").resolve(),
        (REPOSITORY_ROOT / "e2e" / "artifacts").resolve(),
    )
    if not any(output.is_relative_to(root) for root in allowed_roots):
        parser.error("--output must stay under .tmp/ or e2e/artifacts/")

    failures = _array(inputs["failures"], name="failures") if "failures" in inputs else []
    if args.snapshot_url:
        snapshot, snapshot_raw = _snapshot_from_url(
            args.snapshot_url,
            token_env=args.token_env,
            token_header=args.token_header,
            timeout_seconds=args.timeout_seconds,
        )
        artifacts = [_artifact_bytes(snapshot_raw, "snapshot")]
    else:
        snapshot = _object(inputs["snapshot"], name="snapshot")
        artifacts = [_artifact(inputs["snapshot"], "snapshot")]
    artifacts.extend(
        _artifact(path, role)
        for role, path in sorted(inputs.items())
        if role != "snapshot"
    )
    evidence = build_pending_action_baseline(
        snapshot,
        component_revisions=_object(inputs["revisions"], name="revisions"),
        topology=_object(inputs["topology"], name="topology"),
        sdk_discovery=_object(inputs["sdk_discovery"], name="sdk-discovery"),
        sample=_object(inputs["sample"], name="sample"),
        failures=failures,
        artifacts=artifacts,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": str(output), "digest": evidence["digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
