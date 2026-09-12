"""Exercise scoped caller admission on the running local node without editing applications."""

import argparse
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.domain.personalization_access import Grant, ScopeRef, SessionKey, SubjectRef
from adaos.services.personalization_runtime import personalization_access_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Only an enrolled ENV_TYPE=dev node may run this probe")
    args.output.mkdir(parents=True, exist_ok=False)
    init_ctx(Settings.from_sources())
    access = personalization_access_service()
    identity = "e2e-caller-" + uuid4().hex[:12]
    subject, scope = SubjectRef("user", identity), ScopeRef("skill", "builder_sdk_control_skill")
    hub = "http://127.0.0.1:8778"
    owner_headers = {"X-AdaOS-Token": resolve_control_token(base_url=hub)}
    report = {"scope": "live local HTTP admission; no application mutation or Automation pass",
              "session_id": identity, "checks": [], "ok": False}
    def save():
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    def check(name, response, expected):
        report["checks"].append({"name": name, "status": response.status_code, "expected": expected,
                                 "replay": response.headers.get("X-AdaOS-Idempotency-Replay"),
                                 "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 2)})
        save()
        if response.status_code != expected:
            raise RuntimeError(f"{name}: HTTP {response.status_code}, expected {expected}")
    with requests.Session() as client:
        access.put_user(subject, metadata={"test": True, "purpose": "scoped caller E2E"})
        access.put_session(SessionKey(session_id=identity, key_id=identity, subject=subject, expires_at=time.time() + 600))
        access.put_grant(Grant(grant_id=identity, subject=subject, scope=scope, capabilities=("workspace.read",), issued_by=access.owner))
        try:
            issued = client.post(f"{hub}/api/personalization/admin/sessions/{identity}/tool-credential",
                                 headers=owner_headers, json={"skill_name": scope.id, "ttl_seconds": 300}, timeout=30)
            check("owner-issues-existing-session-credential", issued, 200)
            if issued.headers.get("Cache-Control") != "no-store":
                raise RuntimeError("credential response may be cached")
            scoped = {"Authorization": "Bearer " + issued.json()["access_token"]}
            body = {"tool": f"{scope.id}:list_templates", "arguments": {"object_type": "scenario"}, "idempotency_key": identity}
            endpoint = f"{hub}/api/tools/call"
            check("owner-read", client.post(endpoint, headers=owner_headers, json=body, timeout=60), 200)
            first = client.post(endpoint, headers=scoped, json=body, timeout=60)
            check("reader-read-is-not-owner-cache", first, 200)
            if first.headers.get("X-AdaOS-Idempotency-Replay"):
                raise RuntimeError("reader reused the owner's cached response")
            replay = client.post(endpoint, headers=scoped, json=body, timeout=60)
            check("reader-own-replay", replay, 200)
            if replay.headers.get("X-AdaOS-Idempotency-Replay") != "1":
                raise RuntimeError("same caller did not receive an idempotent replay")
            # Missing required write arguments make this harmless even if admission regresses.
            denied = {"tool": f"{scope.id}:save_project_file", "arguments": {"actor": {"role": "owner"}}}
            check("reader-direct-write-denied", client.post(endpoint, headers=scoped, json=denied, timeout=30), 403)
            check("scope-expansion-denied", client.post(endpoint, headers=scoped,
                  json={"tool": "builder_skill:get_session"}, timeout=30), 403)
            check("owner-api-denied", client.post(f"{hub}/api/personalization/admin/sessions/{identity}/tool-credential",
                  headers=scoped, json={"skill_name": "other"}, timeout=30), 401)
            access.revoke_grant(identity, actor=access.owner, reason="E2E revoke before cached replay")
            check("grant-revocation-before-replay", client.post(endpoint, headers=scoped, json=body, timeout=30), 403)
            access.revoke_session(identity, actor=access.owner, reason="E2E session revocation")
            check("session-revocation-before-replay", client.post(endpoint, headers=scoped, json=body, timeout=30), 401)
            report["ok"] = True
        finally:
            # Keep auditable test identities, but never leave an active test grant or credential.
            access.revoke_grant(identity, actor=access.owner, reason="E2E cleanup")
            access.revoke_session(identity, actor=access.owner, reason="E2E cleanup")
            report["cleanup"] = "session and grant revoked; no raw credentials retained"
            save()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
