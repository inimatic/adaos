"""A bounded execution index over the existing E2E and campaign owners."""
from pathlib import Path
import os

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .storage import digest, now, read


def record_run(state, bundle, *, kind, manifest, status, result=None):
    bundle = Path(bundle).resolve()
    key = digest([kind, str(bundle), manifest.get("run_id")])
    path = Path(state) / "maintenance/e2e-runs" / f"{key}.json"
    suite = manifest.get("suite") or {}
    identity = {"manifest_digest": digest(manifest), "suite": suite, "environment": manifest.get("environment", manifest.get("repository", {}))}
    record = {"schema": "adaos.node.e2e_execution.v1", "id": key, "run_id": manifest.get("run_id"),
              "kind": kind, "suite_id": suite.get("suite_id") if isinstance(suite, dict) else str(suite),
              "status": status, "bundle": str(bundle), "identity_digest": digest(identity),
              "started_at": manifest.get("started_at"), "updated_at": now(),
              "summary": (result or {}).get("summary", {}), "reusable": False,
              "reuse_reason": "full_source_test_environment_data_closure_required"}
    with mutation_lock(path.with_suffix(".lock"), timeout_s=2):
        atomic_write_json(path, record)
    return record


def executions(state, *, limit=30):
    root = Path(state) / "maintenance/e2e-runs"
    if not root.is_dir():
        return []
    # Registration order is not retention or evidence authority.
    paths = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 2000:
                break
            if entry.name.endswith(".json") and not entry.is_symlink():
                paths.append(Path(entry.path))
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [read(p) for p in paths[:limit]]


def plan_campaign(service, *, suite_id, node_id, target_build=""):
    from adaos.services.release_validation import get_release_validation_service, ValidationCampaign
    registry = get_release_validation_service()
    current = registry.snapshot()
    suite = next((s for s in current["suites"] if s["suite_id"] == suite_id), None)
    node = next((n for n in current["nodes"] if n["node_id"] == node_id), None)
    if not suite or not node or not node.get("enabled"):
        raise ValueError("An enabled registered test node and suite are required")
    if suite["profile"] != "observe" or "observe" not in node.get("allowed_profiles", []):
        raise ValueError("Only registered observe profiles are allowed")
    if not set(suite["required_capabilities"]).issubset(node.get("capabilities", [])):
        raise ValueError("Test node lacks suite capabilities")
    campaign = ValidationCampaign(campaign_id="planned", suite_id=suite_id,
        node_ids=(node_id,), target_build=target_build, quorum=1)
    record = {"schema": "adaos.node.validation_plan.v1", "suite": suite,
              "test_node_digest": digest(node),
              "test_node_id": node_id, "node_id": service.locations.node_id,
              "target_build": campaign.target_build, "target_policy": campaign.target_policy,
              "created_at": now(), "profile": "observe"}
    key = digest(record)
    atomic_write_json(service.locations.state / "validation-plans" / f"{key}.json", record)
    return {**record, "plan_digest": key}


def apply_campaign(service, plan_digest):
    from adaos.services.release_validation import get_release_validation_service, ValidationCampaign
    if len(str(plan_digest)) != 64 or any(c not in "0123456789abcdef" for c in str(plan_digest)):
        raise ValueError("Invalid validation plan digest")
    plan = read(service.locations.state / "validation-plans" / f"{plan_digest}.json")
    if not plan or digest(plan) != plan_digest or plan["node_id"] != service.locations.node_id:
        raise ValueError("Exact validation plan is unavailable")
    registry = get_release_validation_service()
    current = registry.snapshot()
    suite = next((s for s in current["suites"] if s["suite_id"] == plan["suite"]["suite_id"]), None)
    node = next((n for n in current["nodes"] if n["node_id"] == plan["test_node_id"]), None)
    if digest(suite) != digest(plan["suite"]) or not node or not node["enabled"] or digest(node) != plan["test_node_digest"]:
        raise ValueError("Validation suite or test node changed; plan again")
    campaign_id = "nm-" + plan_digest[:24]
    with mutation_lock(service.locations.state / "validation-apply.lock", timeout_s=2):
        try:
            existing = registry.campaign(campaign_id)
            return {"accepted": True, "campaign_id": campaign_id, "state": existing["state"], "replayed": True}
        except KeyError:
            pass
        registry.create_campaign(ValidationCampaign(campaign_id=campaign_id, suite_id=suite["suite_id"],
            node_ids=(plan["test_node_id"],), target_build=plan["target_build"], target_policy=plan["target_policy"]))
        return {"accepted": True, "campaign_id": campaign_id, "state": "pending", "replayed": False}


def run_pending_campaign(campaign_id):
    from adaos.services.release_validation import get_release_validation_service
    registry = get_release_validation_service()
    if registry.campaign(campaign_id)["state"] != "pending":
        return
    try:
        registry.run_campaign(campaign_id)
    except ValueError as exc:
        if str(exc) != "campaign_already_running":
            raise
