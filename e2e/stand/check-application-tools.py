"""Run a declarative HTTP sequence against one retained test application's skill.

This is an independent installed-behavior probe, never model generation input.
Only its short-lived reader/writer grants are cleaned up; application records
remain available for browser inspection and subsequent update checks.
"""

import argparse
import copy
import hashlib
from pathlib import Path
import re
import time
from uuid import uuid4

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.domain.personalization_access import Grant, ScopeRef, SessionKey, SubjectRef
from adaos.e2e.builder import _expectation_findings, _load_document, _resolve_value, _write_json
from adaos.e2e.builder_lifecycle import _target
from adaos.e2e.stand import redact_value
from adaos.services.agent_context import get_ctx
from adaos.services.personalization_runtime import personalization_access_service


def admitted_skill(manifest, skill):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill) or not any(
        item.get("ref") == f"skill:{skill}" for item in manifest.get("components", {}).get("owned", [])
    ):
        raise ValueError("HTTP probe requires a skill owned by this retained test application")
    return skill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--skill", required=True)
    args = parser.parse_args()
    load_dotenv()
    checkpoint = _load_document(args.checkpoint)
    context = copy.deepcopy(checkpoint["context"])
    root, output = Path(context["bundle_dir"]).resolve(), args.output.resolve()
    if not args.checkpoint.resolve().is_relative_to(root) or not output.is_relative_to(root / "evidence"):
        parser.error("Checkpoint and new evidence must remain inside the original test bundle")
    targets = context["owned_artifacts"]
    if len(targets) != 1:
        parser.error("Select a checkpoint with exactly one owned test application")
    target = targets[0]
    kind, identifier = target["primary_ref"].split(":", 1)
    _target({"object_type": kind, "object_id": identifier}, context)
    init_ctx(Settings.from_sources())
    manifest = _load_document(Path(get_ctx().paths.workspace_dir()) / "projects" / target["project_id"] / "project.yaml")
    skill = admitted_skill(manifest, args.skill)
    plan = _load_document(args.plan)
    if plan.get("schema") != "adaos.e2e.application_tools.v1" or not plan.get("steps"):
        parser.error("A nonempty declarative application tool probe is required")
    output.mkdir(parents=True, exist_ok=False)
    identity = "e2e-app-" + uuid4().hex[:12]
    report = {"scope": "live installed application HTTP behavior; original generation verdict unchanged",
              "skill": skill, "runtime": "workspace", "project_version": manifest.get("version"),
              "parent_sha256": hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
              "plan": plan, "steps": [], "ok": False}
    context["case_instance_id"] = identity
    access, identities = personalization_access_service(), []
    hub = "http://127.0.0.1:8778"
    headers = {"owner": {"X-AdaOS-Token": resolve_control_token(base_url=hub)}, "anonymous": {}}

    def save():
        _write_json(output / "report.json", redact_value(report))

    with requests.Session() as client:
        try:
            for role, capabilities in (("reader", ("workspace.read",)), ("writer", ("workspace.read", "workspace.write"))):
                session_id = f"{identity}-{role}"
                identities.append(session_id)
                subject, scope = SubjectRef("user", session_id), ScopeRef("skill", skill)
                access.put_user(subject, metadata={"test": True, "purpose": "application HTTP E2E"})
                access.put_session(SessionKey(session_id=session_id, key_id=session_id, subject=subject, expires_at=time.time() + 1800))
                access.put_grant(Grant(grant_id=session_id, subject=subject, scope=scope, capabilities=capabilities, issued_by=access.owner))
                issued = client.post(f"{hub}/api/personalization/admin/sessions/{session_id}/tool-credential",
                    headers=headers["owner"], json={"skill_name": skill, "ttl_seconds": 1800}, timeout=30)
                issued.raise_for_status()
                headers[role] = {"Authorization": "Bearer " + issued.json()["access_token"]}
            seen = set()
            for step in plan["steps"]:
                step_id, tool = step["id"], step["tool"]
                if step_id in seen or not re.fullmatch(r"[A-Za-z0-9_]+", tool):
                    raise ValueError("Probe step IDs must be unique and tools must stay inside the owned skill")
                seen.add(step_id)
                body = {"tool": f"{skill}:{tool}", "arguments": _resolve_value(step.get("arguments", {}), context),
                        "dev": False, "idempotency_key": f"{identity}:{step_id}",
                        "context": {"webspace_id": f"e2e-http-{identity}"}}
                report["active_step"] = step_id
                save()
                response = client.post(f"{hub}/api/tools/call", headers=headers[step.get("caller", "writer")],
                                       json=body, timeout=60)
                payload = response.json()
                observed = {"http_status": response.status_code, "body": payload}
                findings = _expectation_findings(observed, _resolve_value(step["expect"], context))
                report["steps"].append({"id": step_id, "caller": step.get("caller", "writer"), "input": body,
                    "output": observed, "findings": findings, "status": "failed" if findings else "passed",
                    "duration_ms": round(response.elapsed.total_seconds() * 1000, 3)})
                report["active_step"] = None
                save()
                print(f"{step_id}: {'failed' if findings else 'passed'}", flush=True)
                if findings:
                    return 1
                context["outputs"][step_id] = observed
            report["ok"] = True
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            for session_id in identities:
                access.revoke_grant(session_id, actor=access.owner, reason="application E2E cleanup")
                access.revoke_session(session_id, actor=access.owner, reason="application E2E cleanup")
            report["cleanup"] = "test credentials/grants revoked; application records retained"
            save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
