"""Record delegated TEST acceptance from independent browser evidence, without starting Codex."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.sdk.builder import workflow
from adaos.services.resources.prototype import prototype_webui_digest


def crud_coverage(webui, samples):
    application = webui["ui"]["application"]
    forms = [widget for modal in application.get("modals", {}).values()
             for widget in modal.get("schema", {}).get("widgets", []) if widget.get("type") == "ui.form"]
    form = next(widget for widget in forms if any(
        action.get("type") == "resourceOperation" and action.get("params", {}).get("operation_id") == "create"
        for action in widget.get("actions", [])))
    resource = next(action["target"] for action in form["actions"]
                    if action.get("type") == "resourceOperation" and action.get("params", {}).get("operation_id") == "create")
    fields = [field["id"] for field in form["inputs"]["fields"] if field["type"] == "shortText"][:2]
    if len(fields) != 2 or not resource.startswith("prototype."):
        return False
    tasks = {f"search/{field}" for field in fields} | {
        "search/empty-result", "choice-filter/change", "create/required-field-rejection/no-mutation",
        "update/required-field-rejection/no-mutation",
        "create/cancel/no-mutation/clear-draft", "create/optional-fields-empty/select-created-record",
        "delete/confirmation-cancel/no-mutation", "delete/confirmation-accept/collection-refresh"}
    return {row["layout"] for row in samples} == {"wide", "compact"} and all(
        not row.get("failure") and not row.get("errors")
        and tasks.issubset({check.get("task") for check in row["checks"]
                           if check.get("status") == "passed" and check.get("resource") == resource})
        and any(check.get("task") == "select/edit/save/reopen" and check.get("status") == "passed"
                and check.get("field") == fields[0] and check.get("resource") == resource for check in row["checks"])
        for row in samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", required=True, type=Path)
    parser.add_argument("--interactions", required=True, type=Path)
    parser.add_argument("--visual", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--additional-interactions", action="append", type=Path, default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    identifier = created["id"]
    if not identifier.startswith("workbench_test_") or created["result"]["project"]["created_by"] != "builder.user":
        parser.error("Owned native UI TEST creation receipt required")
    source = Path(".adaos/dev/sn_6acf0c01/scenarios") / identifier
    raw = (source / "webui.json").read_bytes()
    browser = json.loads(args.interactions.read_text(encoding="utf-8"))
    checkpoint = json.loads((args.interactions.parent / "ui-creation-checkpoint.json").read_text(encoding="utf-8"))
    if browser.get("scenario") != identifier or browser.get("passed") is not True:
        parser.error("Matching successful independent interaction report required")
    if checkpoint["provenance"]["source_sha256"] != hashlib.sha256(raw).hexdigest():
        parser.error("Browser evidence is stale")
    if not crud_coverage(json.loads(raw), browser["samples"]):
        parser.error("Both widths must cover the explicitly qualified scalar CRUD path")
    for path in args.additional_interactions:
        extra = json.loads(path.read_text(encoding="utf-8"))
        provenance = json.loads((path.parent / "ui-creation-checkpoint.json").read_text(encoding="utf-8"))
        if (extra.get("scenario") != identifier or extra.get("passed") is not True
                or provenance["provenance"]["source_sha256"] != hashlib.sha256(raw).hexdigest()
                or {row["layout"] for row in extra.get("samples", [])} != {"wide", "compact"}
                or any(row.get("failure") or row.get("errors") for row in extra["samples"])):
            parser.error("Additional interaction evidence is stale, failed or incomplete")
    visual_report = json.loads(args.visual.read_text(encoding="utf-8"))
    visual = visual_report.get("samples")
    visual_source = json.loads((args.visual.parent / "ui-creation-checkpoint.json").read_text(encoding="utf-8"))
    if visual_report.get("scenario") != identifier or visual_source["provenance"]["source_sha256"] != hashlib.sha256(raw).hexdigest():
        parser.error("Visual evidence is stale or belongs to another application")
    if not isinstance(visual, list) or {row["layout"] for row in visual} != {"wide", "compact"} or any(
        row.get("failure") or row.get("errors") or any(failure["path"].startswith("/api/resources/") for failure in row.get("requestFailures", []))
        or row["geometry"]["currentScenario"] != identifier
        or row["geometry"]["documentWidth"] > row["viewport"]["width"] for row in visual
    ):
        parser.error("Independent visual evidence must match and have no document overflow")
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to((Path.cwd() / "e2e/artifacts/builder").resolve()):
        parser.error("Output must be new and confined to Builder evidence")
    visuals = [{"breakpoint": row["layout"], "viewport": row["viewport"], "status": "passed",
                "evidence_ref": str((args.visual.parent / f"{row['layout']}.png").resolve())} for row in visual]
    if any(not Path(row["evidence_ref"]).is_file() for row in visuals):
        parser.error("Referenced screenshots are missing")
    init_ctx(Settings.from_sources())
    current = workflow.get_state("scenario", identifier)
    if (source / "ui_revisions/current.txt").read_text(encoding="utf-8").strip() != args.revision:
        parser.error("Prototype revision changed during review")
    reviewer = {"id": "agent:codex-independent-test-review", "kind": "agent", "delegated_by": "user:local"}
    checks = [{"id": kind, "status": "passed", "evidence_refs": [str(args.interactions.resolve())]}
              for kind in ["render.ready", "resource.query", "resource.filter", "resource.create", "resource.update", "resource.delete"]]
    next(row for row in checks if row["id"] == "resource.update")["evidence_refs"].extend(
        str(path.resolve()) for path in args.additional_interactions)
    result = workflow.accept_prototype("scenario", identifier, reviewer=reviewer, behavior_checks=checks,
        visual_checks=visuals, actor=reviewer["id"], expected_generation=current.get("generation"),
        acceptance_id=f"acceptance:workbench:{identifier}:{args.revision}")
    _write_json(output, {"scope": "delegated acceptance of this TEST prototype, not human or Automation acceptance",
                         "webui_digest": prototype_webui_digest(json.loads(raw)), "result": result})
    print(json.dumps({"ok": True, "scenario": identifier, "revision": args.revision}, ensure_ascii=False))


if __name__ == "__main__":
    main()
