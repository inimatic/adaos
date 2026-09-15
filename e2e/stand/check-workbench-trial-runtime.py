"""Qualify native runtime admission of the retained, independently reviewed TEST."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.sdk.builder import workflow
from adaos.services.agent_context import get_ctx
from adaos.services.applications.trial_runtime import NativeTrialRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--place", action="store_true")
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
        assert hashlib.sha256(Path(evidence["path"]).read_bytes()).hexdigest() == evidence["sha256"]
    init_ctx(Settings.from_sources())
    current = workflow.get_state("scenario", identifier)
    assert current["automation"]["head_task_id"] == admission["task"]
    delivery = current["delivery"]
    assert delivery["status"] == "trial"
    report = {"scope": "Native Trial runtime admission, not desktop or Trial acceptance", "passed": False}
    try:
        runtime = NativeTrialRuntime.resolve(get_ctx(), delivery["candidate_id"], delivery["release_digest"])
        runtime.manager(prepare=True)
        statuses = {}
        for package in runtime.packages:
            if package.kind == "skill":
                manager = runtime.ready_manager(package.artifact_id)
                statuses[package.artifact_id] = {"identity": runtime.identity(package.artifact_id),
                                                "status": manager.runtime_status(package.artifact_id)}
        report.update(passed=True, runtimes=statuses)
        if args.place:
            from adaos.sdk.builder.applications import place_local_trial, update_application_metadata, publisher_context
            from adaos.sdk.developer import compositions
            from adaos.services.applications import get_application_service

            application = get_application_service(get_ctx().paths.state_dir()).store.get_application(identifier)
            if application.display["title"] == identifier:
                catalog = compositions.get(identifier)["catalog"]
                update_application_metadata(identifier, title=catalog["title"], summary=catalog.get("description", ""),
                    actor_ref="agent:delegated-test-review", subnet_ref=publisher_context()["publisher_ref"],
                    capability="applications.develop", expected_revision=application.revision,
                    idempotency_key="reading-list-trial-catalog-title-repair")

            report["placement"] = place_local_trial(delivery["candidate_id"], webspace_id="desktop",
                actor_ref="agent:delegated-test-review")
            state = workflow.get_state("scenario", identifier)
            previous = next(item for item in state["project"]["placements"]
                            if item["kind"] == "trial" and item["status"] == "active")
            corrected = {key: value for key, value in previous.items()
                         if key not in {"placement_id", "created_at", "updated_at"}}
            corrected["target"] = {**corrected["target"], "webspace_id": "desktop", "space_kind": "workspace"}
            report["builder_placement"] = workflow.record_project_placement(
                "scenario", identifier, corrected, expected_generation=state["generation"])["placement"]
    except Exception as exc:
        report["passed"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _write_json(output, report)
    print(json.dumps({"passed": True, "output": str(output)}))


if __name__ == "__main__":
    main()
