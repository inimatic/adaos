"""Read retained E2E records across node restart and compatible DEV redeployment."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.api.tool_bridge import _skill_manager_for_context
from adaos.domain.personalization_access import Grant, ScopeRef, SessionKey, SubjectRef
from adaos.e2e.builder import _write_json
from adaos.services.agent_context import get_ctx
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.personalization_runtime import personalization_access_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pin", type=Path)
    parser.add_argument("http_evidence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--redeploy", action="store_true")
    parser.add_argument("--delegated-boundary", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or not output.is_relative_to(root) or output.exists():
        parser.error("Requires DEV and new E2E evidence")
    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    evidence = json.loads(args.http_evidence.read_text(encoding="utf-8"))
    scenario, webspace = pin["scenario_id"], pin["preview_webspace_id"]
    if not scenario.startswith("test_") or not webspace.startswith("preview-") or evidence["scenario"] != scenario or not evidence["ok"]:
        raise ValueError("Requires matching successful TEST evidence")
    marker = evidence["marker"]
    if not marker.startswith("E2E-HTTP-"):
        raise ValueError("Only probe-owned records may be inspected")
    init_ctx(Settings.from_sources())
    ctx = get_ctx()
    snapshot = BuilderWorkflowService.from_context().automation_snapshot_root("scenario", scenario)
    metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    if pin.get("stage") != "automation" or metadata.get("object_id") != scenario or metadata.get("task_id") != pin["revision"]:
        raise ValueError("Operations must target the exact retained Automation task")
    skill = scenario + "_skill"
    source = Path(ctx.paths.dev_skills_dir()) / skill
    manager = asyncio.run(_skill_manager_for_context(ctx))
    before = manager.dev_runtime_status(skill)
    source_hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*") if p.is_file() and not any(part in {"__pycache__", ".pytest_cache", ".git"} for part in p.parts)}
    hub = "http://127.0.0.1:8778"
    owner = {"X-AdaOS-Token": resolve_control_token(base_url=hub)}
    report = {"scope": "Retained DEV data; compatible patch redeploy, not schema migration or Trial", "scenario": scenario,
        "task": pin["revision"], "marker": marker, "calls": [], "checks": [], "ok": False,
        "runtime_before": {key: before.get(key) for key in ("version", "active_slot", "ready")}}

    def call(method, **values):
        started = time.perf_counter()
        response = requests.post(hub + "/api/tools/call", headers=owner,
            json={"tool": skill + ":" + method, "arguments": {"webspace_id": webspace, **values}}, timeout=30)
        body = response.json()
        report["calls"].append({"method": method, "ms": (time.perf_counter() - started) * 1000, "status": response.status_code})
        assert response.ok and body.get("ok") is not False and body.get("result", {}).get("ok") is not False
        return body["result"]

    def records():
        assets = call("list_records", kind="assets", search=marker)["items"]
        assert len(assets) == 1 and assets[0]["name"] == marker
        inspections = call("list_records", kind="inspections", parent_id=assets[0]["id"])["items"]
        assert len(inspections) == 1 and inspections[0]["status"] == "completed"
        items = call("list_records", kind="inspection_items", parent_id=inspections[0]["id"])["items"]
        assert len(items) == 1 and items[0]["locked"] is True
        return {"assets": assets, "inspections": inspections, "items": items}

    identities = []
    access = personalization_access_service()
    try:
        report["records"] = records()
        if args.compare:
            previous = json.loads(args.compare.read_text(encoding="utf-8"))
            assert previous["ok"] and previous["scenario"] == scenario and previous["marker"] == marker
            assert previous["records"] == report["records"]
            report["checks"].append("retained-records-exactly-match")
        if args.redeploy:
            installed = manager.prepare_dev_runtime(skill, run_tests=True)
            assert installed.slot != before["active_slot"]
            assert installed.tests and all(result.status == "passed" for result in installed.tests.values())
            report["native_tests"] = {key: value.status for key, value in installed.tests.items()}
            manager.activate_dev_runtime(skill, version=installed.version, slot=installed.slot)
            report["runtime_after"] = {key: manager.dev_runtime_status(skill).get(key) for key in ("version", "active_slot", "ready")}
            assert records() == report["records"]
            report["checks"].append("inactive-slot-redeploy-retains-data")
        if args.delegated_boundary:
            for role, capabilities in (("reader", ("workspace.read",)), ("writer", ("workspace.read", "workspace.write"))):
                identifier = "e2e-dev-boundary-" + uuid4().hex[:12]
                identities.append(identifier)
                subject = SubjectRef("user", identifier)
                access.put_user(subject, metadata={"test": True})
                access.put_session(SessionKey(session_id=identifier, key_id=identifier, subject=subject, expires_at=time.time() + 600))
                access.put_grant(Grant(grant_id=identifier, subject=subject, scope=ScopeRef("skill", skill), capabilities=capabilities, issued_by=access.owner))
                issued = requests.post(f"{hub}/api/personalization/admin/sessions/{identifier}/tool-credential",
                    headers=owner, json={"skill_name": skill, "ttl_seconds": 600}, timeout=30)
                issued.raise_for_status()
                response = requests.post(hub + "/api/tools/call", headers={"Authorization": "Bearer " + issued.json()["access_token"]},
                    json={"tool": skill + ":list_records", "arguments": {"webspace_id": webspace, "kind": "assets"}}, timeout=30)
                assert response.status_code == 403 and response.json().get("detail") == "scoped_caller_dev_runtime_not_supported"
                report["checks"].append(role + "-credential-cannot-enter-personal-dev")
            report["delegated_application_acceptance"] = "blocked: personal DEV rejects delegation; installed workspace qualification awaits delivery decision"
        assert all((source / name).is_file() and hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
            for name, digest in source_hashes.items())
        report["checks"].append("application-source-unchanged")
        report["ok"] = True
    except Exception as exc:
        report["failure"] = str(exc)
        raise
    finally:
        for identifier in identities:
            access.revoke_grant(identifier, actor=access.owner, reason="E2E cleanup")
            access.revoke_session(identifier, actor=access.owner, reason="E2E cleanup")
        _write_json(output, report)
        print(json.dumps({key: report[key] for key in ("scenario", "checks", "ok")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
