"""Inspect the owned native TEST Trial and replay its exact placement without activation."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.sdk.builder import workflow
from adaos.sdk.developer import projects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    root = Path.cwd()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or output.exists() or not output.is_relative_to(root / "e2e/artifacts/builder"):
        parser.error("Requires DEV and new Builder evidence")
    admission = json.loads(args.admission.read_text(encoding="utf-8"))
    identifier = admission["scenario"]
    if not identifier.startswith("workbench_test_") or admission["reviewer"]["kind"] != "agent":
        parser.error("Owned delegated TEST Trial admission required")
    for evidence in admission["reports"]:
        if hashlib.sha256(Path(evidence["path"]).read_bytes()).hexdigest() != evidence["sha256"]:
            parser.error("Independent evidence changed after admission")
    init_ctx(Settings.from_sources())
    current = workflow.get_state("scenario", identifier)
    assert current["automation"]["head_task_id"] == admission["task"]
    delivery = current["delivery"]
    assert delivery["status"] == "trial"
    candidate = projects.get_candidate(delivery["candidate_id"])
    assert candidate["candidate"]["package_digest"] == delivery["package_digest"]
    assert candidate["candidate"]["release_digest"] == delivery["release_digest"]
    placement = next(item for item in current["project"]["placements"] if item["kind"] == "trial" and item["status"] == "active")
    assert placement["result_ref"]["id"] == delivery["candidate_id"]
    assert placement["result_ref"]["digest"] == delivery["package_digest"]
    assert placement["target"]["webspace_id"] == "desktop-dev-dev"
    trial = Path(placement["runtime_binding"]["path"]).resolve()
    assert trial.is_relative_to(root / ".adaos/trials")
    source = root / f".adaos/dev/sn_6acf0c01/scenarios/{identifier}"
    trial_ui = (trial / f"scenarios/{identifier}/webui.json").read_bytes()
    source_ui = (source / "webui.json").read_bytes()
    assert json.loads(trial_ui) == json.loads(source_ui), "Trial changed the approved Automation UI"
    before = (source / "prompt_state.json").read_bytes()
    prepared = next(item for item in reversed(current["history"]) if item["action"] == "candidate_prepared")
    replay = workflow.record_project_placement("scenario", identifier, placement, expected_generation=prepared["generation"])
    assert replay.get("duplicate") is True and replay["placement"] == placement
    assert (source / "prompt_state.json").read_bytes() == before
    _write_json(output, {"scope": "Exact existing Trial identity and no-op replay, not Trial browser acceptance",
        "passed": True, "scenario": identifier, "task": admission["task"], "delivery": delivery,
        "placement": placement, "activation": candidate.get("trial_activation"),
        "replay": {"duplicate": True, "requested_generation": prepared["generation"],
                   "current_generation": replay["workflow"]["generation"]},
        "source_sha256": hashlib.sha256(source_ui).hexdigest(), "trial_webui_sha256": hashlib.sha256(trial_ui).hexdigest(),
        "ui_equivalence": "parsed JSON equality; package formatting may differ"})
    print(json.dumps({"passed": True, "candidate": delivery["candidate_id"], "unchanged": True}))


if __name__ == "__main__":
    main()
