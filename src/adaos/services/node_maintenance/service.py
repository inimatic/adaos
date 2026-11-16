"""One node authority, shared by Management, CLI and owner API."""
from __future__ import annotations

from pathlib import Path

from adaos.services import storage_hygiene
from .inventory import StorageInventory
from .storage import ROLES, StorageLocations, now
from .recovery import catalog


class NodeMaintenanceService:
    def __init__(self, *, workspace, base, node_id):
        self.locations = StorageLocations(workspace, base, node_id)
        self.inventory = StorageInventory(self.locations.state)
        from .migration import StorageMigration
        self.migrations = StorageMigration(self.locations, self.inventory)

    def snapshot(self, *, section="storage"):
        if section == "storage":
            result = self.locations.snapshot()
            for row in result["locations"]:
                sample = self.inventory.snapshot(row["id"], Path(row["active_path"]))
                row.update(inventory=sample, measured_bytes=sample["bytes"], measurement_status=sample["status"])
                row["settings_revision"] = result["revision"]
            result["cleanup"] = storage_hygiene.list_storage_cleanup_jobs(workspace_root=self.locations.workspace, base_dir=self.locations.base, limit=30)
            result["cleanup_jobs"] = result["cleanup"]["jobs"]
            result["migrations"] = self.migrations.snapshot()
            from .worker import observation
            result["worker"] = observation(self.locations.state)
            return result
        if section == "recovery":
            return catalog(self.locations)
        if section == "testing":
            return self.testing_snapshot()
        raise ValueError("Unknown maintenance section")

    def advance_inventory(self, role, *, restart=False):
        if role not in ROLES:
            raise ValueError("Unknown storage role")
        return self.inventory.advance(role, self.locations.resolve(role), restart=restart)

    def run_migration(self, job_id):
        """Wake the continuing owner; HTTP/request budgets never bound a job."""
        self.migrations._path(job_id)
        from .worker import ensure_worker
        ensure_worker(self)
        return next(row for row in self.migrations.snapshot() if row["job_id"] == job_id)

    def run_inventory(self, role):
        """Wake the continuing owner for a previously requested inventory."""
        if role not in ROLES:
            raise ValueError("Unknown storage role")
        from .worker import ensure_worker
        ensure_worker(self)
        return self.inventory.snapshot(role, self.locations.resolve(role))

    def testing_snapshot(self):
        from adaos.services.release_validation import ReleaseValidationService
        from .validation import executions
        # Read a fresh node-authority snapshot, including when called from a
        # Trial worker. Never create a second campaign writer in that process.
        current = ReleaseValidationService(state_path=self.locations.base / "state/release_validation/state.json").snapshot()
        # Never expose node SSH/key paths, raw events or provider request bodies.
        def project(rows, keys):
            return [{k: row.get(k) for k in keys} for row in rows[:30]]
        return {"observed_at": now(), "summary": current.get("summary", {}),
                "e2e_runs": executions(self.locations.base / "state"),
                "suites": project(current.get("suites", []), ("suite_id", "version", "display_name", "profile", "timeout_s")),
                "nodes": project(current.get("nodes", []), ("node_id", "display_name", "enabled", "capabilities")),
                "campaigns": project(current.get("campaigns", []), ("campaign_id", "suite_id", "state", "target_build", "created_at", "updated_at", "result")),
                "assignments": project(current.get("assignments", []), ("assignment_id", "campaign_id", "node_id", "state", "updated_at"))}


def from_context(ctx=None):
    from adaos.services.agent_context import get_ctx
    ctx = ctx or get_ctx()
    owner = getattr(ctx, "authority_context", None) or ctx
    return NodeMaintenanceService(workspace=owner.paths.repo_root(), base=owner.paths.base_dir(),
                                  node_id=owner.config.node_id_value)
