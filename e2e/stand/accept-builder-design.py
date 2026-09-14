"""Record explicit user acceptance of the pinned Builder design, without execution."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.sdk.builder import workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    load_dotenv()
    if os.environ.get("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report.get("passed") is not True or report.get("errors"):
        parser.error("A passing browser report is required")
    if not all(row.get("passed") is True for row in report["checks"]):
        parser.error("Browser checks contain failures")
    init_ctx(Settings.from_sources())
    current = workflow.get_state("scenario", "builder")
    if current["prototype"]["head_revision"] != args.revision:
        parser.error("Current prototype differs from the reviewed revision")
    if current["prototype"].get("acceptance"):
        parser.error("Prototype already has acceptance; do not duplicate the decision")
    result = workflow.accept_prototype(
        "scenario", "builder",
        reviewer={"id": "codex:recording-user-decision", "kind": "agent",
                  "delegated_by": "conversation:user:builder-design-acceptance-20260914"},
        behavior_checks=[{"id": "render.ready", "status": "passed", "evidence_refs": [str(args.report)]},
                         *[{"id": f"{row['profile']}:{row['check']}", "status": "passed",
                          "evidence_refs": [str(args.report)]} for row in report["checks"]]],
        visual_checks=[{"breakpoint": name, "viewport": {"width": width, "height": height},
                        "status": "passed", "evidence_ref": str(args.report.parent / f"{name}-initial.png")}
                       for name, width, height in [("wide", 1440, 1000), ("compact", 390, 844)]],
        actor="codex.record_explicit_user_acceptance",
        expected_generation=current["generation"],
    )
    _write_json(args.output, {"user_decision": args.decision, "revision": args.revision,
                              "report": str(args.report), "result": result})
    print(json.dumps({"accepted": result["acceptance"]["revision"],
                      "generation": result["workflow"]["generation"], "receipt": str(args.output)}))


if __name__ == "__main__":
    main()
