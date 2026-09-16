"""Exercise the live Users & Access Root MCP surface with reversible mutations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import requests
from dotenv import load_dotenv

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", default="http://127.0.0.1:8777")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    root = Path.cwd().resolve()
    output = args.output.resolve()
    artifacts = (root / "e2e" / "artifacts").resolve()
    if (
        os.getenv("ENV_TYPE") != "dev"
        or output.exists()
        or not output.is_relative_to(artifacts)
    ):
        parser.error("A new output below e2e/artifacts and ENV_TYPE=dev are required")

    report: dict[str, Any] = {
        "schema": "adaos.e2e.users_access.v1",
        "scope": "live owner Users & Access read and reversible invitation lifecycle",
        "read_only": bool(args.read_only),
        "calls": [],
        "checks": [],
        "passed": False,
    }
    invite_id: str | None = None
    subnet_id = ""

    with requests.Session() as client:
        client.trust_env = False
        client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=args.hub)

        def call(tool_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
            started = perf_counter()
            response = client.post(
                args.hub + "/api/admin/root_mcp/call",
                json={"tool_id": tool_id, "arguments": arguments, "dry_run": False},
                timeout=30,
            )
            body = response.json()
            report["calls"].append(
                {
                    "tool_id": tool_id,
                    "status": response.status_code,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                    "ok": bool(response.ok and body.get("ok")),
                }
            )
            response.raise_for_status()
            if not body.get("ok") or not (body.get("response") or {}).get("ok"):
                raise RuntimeError(f"{tool_id} returned an unsuccessful MCP envelope")
            nonlocal subnet_id
            subnet_id = subnet_id or str((body.get("scope") or {}).get("subnet_id") or "")
            return dict((body.get("response") or {}).get("result") or {})

        def check(identifier: str, condition: bool) -> None:
            report["checks"].append(
                {"id": identifier, "status": "passed" if condition else "failed"}
            )
            if not condition:
                raise AssertionError(identifier)

        try:
            summary = call(
                "users_access.summary",
                {
                    "sections": [
                        "people",
                        "guests",
                        "children",
                        "devices",
                        "sessions",
                        "application_access",
                        "invites",
                        "audit",
                    ],
                    "detail": "compact",
                    "audit_limit": 20,
                },
            )
            surface = summary.get("users_access") or {}
            administration = summary.get("administration") or {}
            check(
                "summary-has-user-sections",
                all(
                    isinstance(surface.get(key), list)
                    for key in (
                        "people",
                        "guests",
                        "children",
                        "devices",
                        "sessions",
                        "application_access",
                    )
                ),
            )
            check(
                "summary-has-administration-sections",
                isinstance(administration.get("invites"), list)
                and isinstance(administration.get("audit"), list),
            )
            check("summary-is-redacted", bool((surface.get("diagnostics") or {}).get("content_redacted")))
            check("owner-scope-has-subnet", bool(subnet_id))

            if not args.read_only:
                marker = f"e2e-users-access-{uuid4().hex}"
                invitation = call(
                    "users_access.create_invite",
                    {
                        "kind": "guest",
                        "role": "guest",
                        "scope_kind": "subnet",
                        "scope_id": subnet_id,
                        "expires_in_minutes": 10,
                        "max_sessions": 1,
                        "idempotency_key": marker,
                    },
                )
                invite = invitation.get("invite") or {}
                invite_id = str(invite.get("invite_id") or "")
                check(
                    "create-invitation",
                    bool(invite_id) and invite.get("status") == "pending" and not invitation.get("duplicate"),
                )
                duplicate = call(
                    "users_access.create_invite",
                    {
                        "kind": "guest",
                        "role": "guest",
                        "scope_kind": "subnet",
                        "scope_id": subnet_id,
                        "expires_in_minutes": 10,
                        "max_sessions": 1,
                        "idempotency_key": marker,
                    },
                )
                check(
                    "invitation-idempotency",
                    bool(duplicate.get("duplicate"))
                    and str((duplicate.get("invite") or {}).get("invite_id") or "") == invite_id,
                )
                revoked = call(
                    "users_access.revoke_invite",
                    {
                        "invite_id": invite_id,
                        "reason": "Users & Access E2E cleanup",
                        "idempotency_key": marker + ":revoke",
                    },
                )
                check(
                    "revoke-invitation",
                    (revoked.get("invite") or {}).get("status") == "revoked",
                )
                invite_id = None
                final_summary = call(
                    "users_access.summary",
                    {"sections": ["invites", "audit"], "detail": "compact", "audit_limit": 50},
                )
                revoked_id = str((revoked.get("invite") or {}).get("invite_id") or "")
                retained = next(
                    (
                        item
                        for item in (final_summary.get("administration") or {}).get("invites") or []
                        if str(item.get("invite_id") or "") == revoked_id
                    ),
                    {},
                )
                check(
                    "revoked-invitation-visible-with-status",
                    retained.get("status") == "revoked",
                )

            report["passed"] = bool(report["checks"]) and all(
                item["status"] == "passed" for item in report["checks"]
            )
        except Exception as exc:
            report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            if invite_id:
                try:
                    call(
                        "users_access.revoke_invite",
                        {
                            "invite_id": invite_id,
                            "reason": "Users & Access failed E2E cleanup",
                            "idempotency_key": f"cleanup:{invite_id}",
                        },
                    )
                    report["cleanup"] = "invitation_revoked"
                except Exception as cleanup_exc:
                    report["cleanup_failure"] = f"{type(cleanup_exc).__name__}: {cleanup_exc}"

    _write_json(output, report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(report["checks"]),
                "output": str(output),
                "failure": report.get("failure"),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
