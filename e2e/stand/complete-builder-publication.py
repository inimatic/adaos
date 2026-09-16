"""Promote one independently verified local Builder Trial to Stable."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json


def _refs(values: list[str]) -> list[str]:
    refs = [str(value).strip() for value in values if str(value).strip()]
    if not refs:
        raise ValueError("at least one evidence reference is required")
    return refs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--regression", action="append", default=[])
    parser.add_argument("--access-matrix", action="append", default=[])
    parser.add_argument("--pending-action", action="append", default=[])
    parser.add_argument("--audit", action="append", default=[])
    parser.add_argument("--disclosure", action="append", default=[])
    parser.add_argument("--redaction", action="append", default=[])
    parser.add_argument("--webspace", default="desktop-dev")
    parser.add_argument("--hub", default="http://127.0.0.1:8778")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    load_dotenv()
    root = Path.cwd().resolve()
    output = args.output.resolve()
    artifacts_root = (root / "e2e" / "artifacts" / "builder").resolve()
    if (
        os.getenv("ENV_TYPE") != "dev"
        or output.exists()
        or not output.is_relative_to(artifacts_root)
        or "_test_" not in args.project.casefold()
    ):
        parser.error("A new confined DEV evidence path and owned TEST project are required")

    evidence = {
        "source_commit": args.source_commit.strip(),
        "observed_capabilities": _refs(args.capability),
        "inferred_capabilities": _refs(args.capability),
        "regression_evidence": _refs(args.regression),
        "access_matrix_evidence": _refs(args.access_matrix),
        "pending_action_evidence": _refs(args.pending_action),
        "audit_evidence": _refs(args.audit),
        "disclosure_evidence": _refs(args.disclosure),
        "redaction_evidence": _refs(args.redaction),
        "release_scope": "publication",
    }
    command: dict[str, Any] = {
        "tool": "builder_sdk_control_skill:publish_project",
        "dev": True,
        "arguments": {
            "object_type": "project",
            "object_id": args.project,
            "dry_run": False,
            "confirmed": True,
            "verification_evidence": evidence,
            "webspace_id": args.webspace,
        },
        "context": {
            "webspace_id": args.webspace,
            "action_approval": {
                "status": "approve",
                "approved_by": "agent:delegated-test-review",
                "risk_class": "network",
            },
        },
    }
    report: dict[str, Any] = {
        "schema": "adaos.e2e.builder.publication.v1",
        "scope": "Builder Stable publication after independent Trial verification",
        "project_id": args.project,
        "command": command,
        "passed": False,
    }
    try:
        with requests.Session() as client:
            client.trust_env = False
            client.headers["X-AdaOS-Token"] = resolve_control_token(
                base_url=args.hub
            )
            response = client.post(
                args.hub + "/api/tools/call",
                json=command,
                timeout=300,
            )
            report["http_status"] = response.status_code
            report["response"] = response.json()
            response.raise_for_status()
            result = report["response"].get("result") or {}
            verification = result.get("application_verification") or {}
            workflow = result.get("workflow") or {}
            if not report["response"].get("ok") or not result.get("ok", True):
                raise RuntimeError("Builder publication command did not complete")
            if verification.get("status") != "passed":
                raise RuntimeError("Publication access verification did not pass")
            if (workflow.get("delivery") or {}).get("status") != "published":
                raise RuntimeError("Builder workflow did not reach published delivery")
            report["candidate_id"] = (workflow.get("delivery") or {}).get(
                "candidate_id"
            )
            report["release"] = (workflow.get("publication") or {}).get("release")
            report["verification_report_digest"] = (
                (verification.get("verification") or {}).get("report") or {}
            ).get("report_digest")
            report["passed"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output, report)

    print(
        json.dumps(
            {
                "passed": report["passed"],
                "failure": report.get("failure"),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
