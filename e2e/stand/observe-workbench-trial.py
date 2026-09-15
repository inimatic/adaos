"""Observe native Builder Trial delivery without placing or activating it via API."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition
from adaos.services.applications.store import ApplicationStore
from adaos.services.applications.service import ApplicationService
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    root = Path.cwd().resolve()
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    admission = json.loads(args.admission.read_text(encoding="utf-8"))
    response = json.loads(args.result.read_text(encoding="utf-8"))
    identifier = created["id"]
    output = args.output.resolve()
    if (os.getenv("ENV_TYPE") != "dev" or not identifier.startswith("workbench_test_")
            or created["result"]["project"]["created_by"] != "builder.user"
            or admission["scenario"] != identifier or output.exists()
            or not output.is_relative_to(root / "e2e/artifacts/builder")):
        parser.error("Owned native TEST receipts and new confined output required")
    report = {"scope": "Read-only proof of native Builder Beta cutover, not acceptance or manual placement",
              "scenario": identifier, "task": admission["task"], "passed": False}
    try:
        for reference in admission["reports"]:
            assert hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest() == reference["sha256"]
        assert response["ok"] and response["result"]["trial_ready"]
        observed = response["result"]["workflow"]
        current = json.loads((root / f".adaos/dev/sn_6acf0c01/scenarios/{identifier}/prompt_state.json").read_text(encoding="utf-8"))["workflow"]
        assert current["automation"]["head_task_id"] == admission["task"]
        delivery = current["delivery"]
        assert delivery["status"] == "trial" and delivery == observed["delivery"]
        candidate = delivery["candidate_id"]
        placements = [item for item in current["project"]["placements"]
                      if item["kind"] == "trial" and item["status"] == "active"
                      and item["result_ref"]["id"] == candidate]
        assert len(placements) == 1
        placement = placements[0]
        assert placement["target"]["space_kind"] == "workspace"
        state = root / ".adaos/state"
        store = ApplicationStore(state)
        selections = [item for item in store.list_runtime_selections() if item.application_id == identifier]
        assert selections and all(item.source == "local_trial" and item.release_digest == delivery["release_digest"]
                                  and item.runtime_root_ref == "trial:" + candidate for item in selections)
        selection = next(item for item in selections if item.webspace_id == placement["target"]["webspace_id"])
        activation = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate)
        assert activation["data_mode"] == placement["data_mode"] == "snapshot"
        proof = activation["safety_evidence"]["data_transition"]
        assert proof == placement["safety"]["data_transition"]
        operation_id = f"application-beta:{identifier}:{candidate}"
        transition = ApplicationRuntimeTransition(ApplicationRuntimeChannel(state, identifier)).get(operation_id)
        assert proof["operation_id"] == operation_id and transition["completed"]
        assert transition["intent"]["contract_digest"] == proof["contract_digest"]
        assert set(transition["receipts"]) == {"snapshot_migrate_data", "inherit_configuration", "activate_verify"}
        model = next(item for item in ApplicationService(store).list_models()
                     if item["application"]["application_id"] == identifier)
        assert model["local_beta_active"] and model["use_prerelease"]
        hub = "http://127.0.0.1:8778"
        catalog = requests.get(hub + "/api/node/yjs/webspaces/" + selection.webspace_id + "/catalog/apps",
                               headers={"X-AdaOS-Token": resolve_control_token(base_url=hub)}, timeout=30)
        catalog.raise_for_status()
        launchers = [item for item in catalog.json()["items"] if item.get("scenario_id") == identifier]
        assert len(launchers) == 1 and launchers[0]["release_stage"] == "beta"
        assert launchers[0]["component_update"]["candidate"]["id"] == candidate
        report.update(passed=True, builder_placement=placement, delivery=delivery,
                      placement={"runtime_selection": selection.to_dict()},
                      operation={"id": operation_id, "completed": True, "contract_digest": proof["contract_digest"],
                                 "steps": list(transition["receipts"])},
                      all_selected_webspaces=[item.webspace_id for item in selections],
                      desktop={"count": len(launchers), "release_stage": "beta", "execution": "requires-independent-review"},
                      flags={key: model[key] for key in ("use_prerelease", "local_beta_active", "prerelease_following")})
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        _write_json(output, report)
    print(json.dumps({key: report.get(key) for key in ("passed", "scenario", "operation", "failure")}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
