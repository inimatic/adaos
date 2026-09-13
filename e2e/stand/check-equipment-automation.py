"""Independent DEV-owner HTTP acceptance; never supplied as model input."""

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
from adaos.e2e.builder import _write_json
from adaos.services.builder.workflow import BuilderWorkflowService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pin", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--contract", choices=("separate-parent", "embedded-parent"), default="separate-parent")
    args = parser.parse_args()
    load_dotenv()
    root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or not output.is_relative_to(root) or output.exists():
        parser.error("Requires DEV and new evidence inside e2e/artifacts/builder")
    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    scenario = pin["scenario_id"]
    webspace = pin["preview_webspace_id"]
    if pin["stage"] != "automation" or not scenario.startswith("test_") or not webspace.startswith("preview-"):
        parser.error("Requires an explicitly pinned TEST Automation preview")
    init_ctx(Settings.from_sources())
    snapshot = BuilderWorkflowService.from_context().automation_snapshot_root("scenario", scenario)
    metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    if metadata.get("object_id") != scenario or metadata.get("task_id") != pin["revision"]:
        parser.error("The pinned Automation is no longer the current retained candidate")
    hub = "http://127.0.0.1:8778"
    client = requests.Session()
    client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)
    report = {"scope": "Actual DEV-owner tools; not delegated reader/writer acceptance", "scenario": scenario,
              "task": pin["revision"], "checks": [], "calls": [], "ok": False,
              "records": "New labelled E2E records retained; existing user data untouched", "contract": args.contract}

    def call(method, **values):
        if args.contract == "embedded-parent":
            if method == "save_record":
                fields = dict(values.get("values") or {})
                parent = values.pop("parent_id", None)
                if parent is not None:
                    fields.setdefault("asset_id" if values["kind"] == "inspections" else "inspection_id", parent)
                values["values"] = fields
                if values.pop("complete", False):
                    values["command"] = "complete"
            elif method == "delete_record":
                values.pop("token", None)
        started = time.perf_counter()
        response = client.post(hub + "/api/tools/call", json={"tool": scenario + "_skill:" + method,
            "arguments": {"webspace_id": webspace, **values}}, timeout=30)
        body = response.json()
        report["calls"].append({"tool": method, "input": values, "status": response.status_code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "response": body})
        assert response.ok and body.get("ok") is not False, body
        return body.get("result", body)

    def save(kind, **values):
        return call("save_record", kind=kind, token=str(uuid4()), **values)

    def check(name, predicate):
        assert predicate, name
        report["checks"].append({"id": name, "status": "passed"})
        print(name + ": passed", flush=True)

    marker = "E2E-HTTP-" + str(uuid4())[:8]
    report["marker"] = marker
    try:
        response = requests.post(hub + "/api/tools/call", json={"tool": scenario + "_skill:list_records",
            "arguments": {"webspace_id": webspace, "kind": "assets"}}, timeout=30)
        check("unauthenticated-ingress-denied", response.status_code in (401, 403))
        create = {"kind": "assets", "values": {"name": marker, "location": "HTTP test"}, "token": str(uuid4())}
        asset = call("save_record", **create)["item"]
        retried = call("save_record", **create)["item"]
        check("create-retry-no-duplicate", retried["id"] == asset["id"] and
              len(call("list_records", kind="assets", search=marker)["items"]) == 1)
        edited = save("assets", id=asset["id"], revision=asset["revision"], values={"location": "Updated"})["item"]
        stale = save("assets", id=asset["id"], revision=asset["revision"], values={"location": "Stale"})
        check("stale-revision-no-overwrite", stale.get("ok") is False and
              call("get_record", kind="assets", id=asset["id"])["item"]["location"] == "Updated")
        missing = save("inspections", parent_id=str(uuid4()), values={"note": marker})
        check("missing-parent-denied", missing.get("ok") is False)
        inspection = save("inspections", parent_id=asset["id"], values={"note": marker, "started_at": "2026-09-13"})["item"]
        mismatch = save("inspections", id=inspection["id"], revision=inspection["revision"],
                        parent_id=asset["id"], values={"asset_id": str(uuid4())})
        check("parent-mismatch-denied", mismatch.get("ok") is False)
        deleted = call("delete_record", kind="assets", id=asset["id"], revision=edited["revision"], token=str(uuid4()))
        check("dependent-delete-denied", deleted.get("ok") is False and
              call("get_record", kind="inspections", id=inspection["id"])["item"]["asset_id"] == asset["id"])
        item = save("inspection_items", parent_id=inspection["id"], values={"title": marker + " defect", "result": "defect"})["item"]
        completed = save("inspections", id=inspection["id"], revision=inspection["revision"], parent_id=asset["id"], complete=True)
        persisted = call("get_record", kind="inspections", id=inspection["id"])["item"]
        check("completion-atomic-with-problem-items", completed.get("ok") is False and
              any(problem["id"] == item["id"] for problem in completed.get("problems" if args.contract == "embedded-parent" else "items", [])) and
              persisted["status"] == "draft" and persisted["revision"] == inspection["revision"])
        item = save("inspection_items", id=item["id"], revision=item["revision"], parent_id=inspection["id"],
                    values={"comment": "Confirmed defect"})["item"]
        completed = save("inspections", id=inspection["id"], revision=inspection["revision"], parent_id=asset["id"], complete=True)["item"]
        check("valid-completion", completed["status"] == "completed" and bool(completed["completed_at"]))
        reopen = save("inspections", id=inspection["id"], revision=completed["revision"], parent_id=asset["id"], values={"status": "draft"})
        mutate_child = save("inspection_items", id=item["id"], revision=item["revision"], parent_id=inspection["id"], values={"comment": "Must not overwrite"})
        new_child = save("inspection_items", parent_id=inspection["id"], values={"title": "Must not create"})
        check("completed-parent-and-children-server-locked", all(result.get("ok") is False for result in (reopen, mutate_child, new_child)) and
              call("get_record", kind="inspection_items", id=item["id"])["item"]["comment"] == "Confirmed defect")
        check("no-selection-no-unrelated-children", call("list_records", kind="inspections")["items"] == [] and
              call("list_records", kind="inspection_items")["items"] == [])
        check("server-status-and-result-filters", call("list_records", kind="inspections", parent_id=asset["id"], status="draft")["items"] == [] and
              len(call("list_records", kind="inspections", parent_id=asset["id"], status="completed")["items"]) == 1 and
              call("list_records", kind="inspection_items", parent_id=inspection["id"], result="ok")["items"] == [] and
              len(call("list_records", kind="inspection_items", parent_id=inspection["id"], result="defect")["items"]) == 1)
        report["ok"] = True
    except Exception as error:
        report["failure"] = str(error)
        raise
    finally:
        client.close()
        _write_json(output, report)


if __name__ == "__main__":
    main()
