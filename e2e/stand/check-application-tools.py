"""Run a declarative HTTP sequence against one retained test application's skill.

This is an independent installed-behavior probe, never model generation input.
Only its short-lived reader/writer grants are cleaned up; application records
remain available for browser inspection and subsequent update checks.
"""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
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
from adaos.services.personalization_runtime import personalization_access_service


def admitted_skill(manifest, skill):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill) or not any(
        item.get("ref") == f"skill:{skill}" for item in manifest.get("components", {}).get("owned", [])
    ):
        raise ValueError("HTTP probe requires a skill owned by this retained test application")
    return skill


def runtime_application_target(project_id, skill):
    """Resolve either installed Workspace source or the exact selected local Trial."""

    from adaos.services.applications import get_application_service
    from adaos.services.agent_context import get_ctx

    ctx = get_ctx()
    service = get_application_service(Path(ctx.paths.state_dir()))
    applications = [
        item
        for item in service.store.list_applications()
        if item.legacy_project_id == project_id
    ]
    if len(applications) > 1:
        raise ValueError("HTTP probe found ambiguous Application ownership")
    application = applications[0] if applications else None
    manifest_path = Path(ctx.paths.workspace_dir()) / "projects" / project_id / "project.yaml"
    if manifest_path.is_file():
        manifest = _load_document(manifest_path)
        release_digest = None
        if application is not None:
            installations = [
                item
                for item in service.store.list_installations()
                if item.application_id == application.application_id
                and item.status == "active"
            ]
            if len(installations) != 1:
                raise ValueError(
                    "Installed Application HTTP probe requires one active installation"
                )
            release_digest = installations[0].installed_release_digest
        return {
            "skill": admitted_skill(manifest, skill),
            "runtime": "workspace",
            "project_version": manifest.get("version"),
            "application_id": application.application_id if application else None,
            "release_digest": release_digest,
        }
    if len(applications) != 1:
        raise ValueError(
            "HTTP probe requires one Application aggregate for a Trial-only Project"
        )
    application = applications[0]
    selections = [
        item
        for item in service.store.list_runtime_selections()
        if item.application_id == application.application_id
        and item.source == "local_trial"
    ]
    if len(selections) != 1:
        raise ValueError(
            "HTTP probe requires one exact active local Trial runtime selection"
        )
    selection = selections[0]
    release = service.store.get_release(
        application.application_id,
        selection.release_digest,
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill) or not any(
        item.kind == "skill" and item.artifact_id == skill
        for item in release.project_release.components
    ):
        raise ValueError(
            "HTTP probe requires a skill owned by the selected Trial release"
        )
    return {
        "skill": skill,
        "runtime": "trial",
        "project_version": release.project_release.version,
        "application_id": application.application_id,
        "release_digest": release.release_digest,
    }


def observe_response(response):
    return {"http_status": response.status_code, "body": response.json()}


def race_summary(results):
    def successful(item):
        body = item["body"]
        result = body.get("result", body)
        return (
            200 <= item["http_status"] < 300
            and body.get("ok") is not False
            and (not isinstance(result, dict) or result.get("ok") is not False)
        )

    return {
        "results": results,
        "http_statuses": sorted(item["http_status"] for item in results),
        "success_count": sum(successful(item) for item in results),
    }


def concurrent_calls(hub, headers, bodies):
    from threading import Barrier

    barrier = Barrier(len(bodies), timeout=10)

    def request(body):
        with requests.Session() as client:
            barrier.wait()
            return observe_response(
                client.post(
                    f"{hub}/api/tools/call",
                    headers=headers,
                    json=body,
                    timeout=60,
                )
            )

    with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
        return race_summary(list(executor.map(request, bodies)))


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
    runtime_target = runtime_application_target(target["project_id"], args.skill)
    skill = runtime_target["skill"]
    plan = _load_document(args.plan)
    if plan.get("schema") != "adaos.e2e.application_tools.v1" or not plan.get("steps"):
        parser.error("A nonempty declarative application tool probe is required")
    output.mkdir(parents=True, exist_ok=False)
    identity = "e2e-app-" + uuid4().hex[:12]
    report = {"scope": "live installed application HTTP behavior; original generation verdict unchanged",
              "skill": skill, "runtime": runtime_target["runtime"],
              "project_version": runtime_target["project_version"],
              "application_id": runtime_target.get("application_id"),
              "release_digest": runtime_target["release_digest"],
              "parent_sha256": hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
              "plan": plan, "steps": [], "ok": False}
    context["case_instance_id"] = identity
    access, identities, application_grants = personalization_access_service(), [], []
    hub = "http://127.0.0.1:8778"
    headers = {"owner": {"X-AdaOS-Token": resolve_control_token(base_url=hub)}, "anonymous": {}}
    application_management = None
    if runtime_target.get("application_id"):
        from adaos.services.applications import (
            ApplicationAccessManagementService,
            get_application_service,
        )
        from adaos.services.agent_context import get_ctx

        application_management = ApplicationAccessManagementService(
            get_application_service(Path(get_ctx().paths.state_dir()))
        )
        caller_contracts = plan.get("callers")
        if not isinstance(caller_contracts, dict):
            raise ValueError(
                "Access-aware Application probes must declare reader/writer Application roles"
            )

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
                if application_management is not None:
                    caller = caller_contracts.get(role)
                    if not isinstance(caller, dict):
                        raise ValueError(
                            f"Access-aware Application probe is missing callers.{role}"
                        )
                    grant = application_management.access.grant_access(
                        runtime_target["application_id"],
                        release_digest=runtime_target["release_digest"],
                        subject_ref=f"user:{session_id}",
                        application_roles=tuple(caller.get("application_roles") or ()),
                        permission_ceiling=tuple(caller.get("permission_ceiling") or ()),
                        issuer_ref="system:builder-e2e",
                        idempotency_key=f"{identity}:{role}",
                        constraints={
                            "subject_kind": "user",
                            "platform_role": str(caller.get("platform_role") or "member"),
                        },
                    )
                    application_grants.append(grant)
            probe_webspace = str(plan.get("webspace_id") or "").strip()
            if not probe_webspace:
                probe_webspace = (
                    "desktop"
                    if runtime_target.get("application_id")
                    else f"e2e-http-{identity}"
                )
            seen = set()
            for step in plan["steps"]:
                step_id, tool = step["id"], step["tool"]
                if step_id in seen or not re.fullmatch(r"[A-Za-z0-9_]+", tool):
                    raise ValueError("Probe step IDs must be unique and tools must stay inside the owned skill")
                seen.add(step_id)
                raw_arguments = step.get("concurrent_arguments")
                if raw_arguments is None:
                    raw_arguments = [step.get("arguments", {})]
                elif (
                    "arguments" in step
                    or not isinstance(raw_arguments, list)
                    or not 2 <= len(raw_arguments) <= 4
                    or not all(isinstance(item, dict) for item in raw_arguments)
                ):
                    raise ValueError(
                        "A race requires two to four argument objects and no sequential arguments"
                    )
                bodies = [
                    {
                        "tool": f"{skill}:{tool}",
                        "arguments": _resolve_value(item, context),
                        "dev": False,
                        "idempotency_key": f"{identity}:{step_id}:{index}",
                        "context": {"webspace_id": probe_webspace},
                    }
                    for index, item in enumerate(raw_arguments)
                ]
                report["active_step"] = step_id
                save()
                credential = headers[step.get("caller", "writer")]
                started_at = time.perf_counter()
                if "concurrent_arguments" in step:
                    observed = concurrent_calls(hub, credential, bodies)
                else:
                    observed = observe_response(
                        client.post(
                            f"{hub}/api/tools/call",
                            headers=credential,
                            json=bodies[0],
                            timeout=60,
                        )
                    )
                findings = _expectation_findings(observed, _resolve_value(step["expect"], context))
                report["steps"].append({"id": step_id, "caller": step.get("caller", "writer"),
                    "input": bodies if len(bodies) > 1 else bodies[0],
                    "output": observed, "findings": findings, "status": "failed" if findings else "passed",
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 3)})
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
            if application_management is not None:
                for grant in application_grants:
                    application_management.access.revoke_access(
                        grant.grant_id,
                        issuer_ref="system:builder-e2e",
                        expected_revision=grant.revision,
                    )
            for session_id in identities:
                access.revoke_grant(session_id, actor=access.owner, reason="application E2E cleanup")
                access.revoke_session(session_id, actor=access.owner, reason="application E2E cleanup")
            report["cleanup"] = "test credentials/grants revoked; application records retained"
            save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
