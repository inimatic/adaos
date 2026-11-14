"""Revisioned placement intent. Saving settings never moves existing data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

ROLES = {
    "application_recovery": ("base", "recovery/applications", "Application recovery", "applications"),
    "operator_recovery": ("base", "recovery/private-runtime", "Operator recovery evidence", "operator"),
    "private_recovery": ("base", "state/private_recovery", "Infrastructure recovery", "root-operations"),
    "builder_recovery": ("base", "state/builder/source_recovery", "Builder source recovery", "builder"),
    "e2e_evidence": ("workspace", "e2e/artifacts", "E2E evidence", "e2e"),
    "stand_evidence": ("workspace", "artifacts/e2e-runs", "Stand evidence", "release-validation"),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def read(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    if not isinstance(value, dict):
        raise ValueError("Maintenance record must be an object")
    return value


def ancestor(path):
    candidate = Path(path)
    while not candidate.exists():
        if candidate == candidate.parent:
            raise ValueError("Storage volume is unavailable")
        candidate = candidate.parent
    return candidate


def volume(path):
    """Read capacity without creating, probing or enumerating destination data."""
    nearest = ancestor(path)
    info = nearest.stat()
    capacity = shutil.disk_usage(nearest)
    mount = nearest
    while mount.parent != mount and mount.parent.exists() and mount.parent.stat().st_dev == info.st_dev:
        mount = mount.parent
    return {"id": str(info.st_dev), "mount": str(mount), "total_bytes": capacity.total,
            "free_bytes": capacity.free, "observed_at": now(), "available": True,
            "write_access": "not_probed", "existing_ancestor": str(nearest)}


class StorageLocations:
    def __init__(self, workspace, base, node_id):
        self.workspace, self.base = Path(workspace).resolve(), Path(base).resolve()
        self.node_id = str(node_id)
        self.state = self.base / "state/maintenance"
        self.settings_path = self.state / "storage-settings.json"

    def defaults(self):
        return {role: {"active_path": str((self.base if spec[0] == "base" else self.workspace) / spec[1]),
                       "desired_path": None, "quota_bytes": None, "min_free_bytes": 20*1024**3}
                for role, spec in ROLES.items()}

    def settings(self):
        record = read(self.settings_path)
        if record is None:
            return {"schema": "adaos.node.storage_settings.v1", "node_id": self.node_id,
                    "revision": 0, "roles": self.defaults()}
        if record.get("schema") != "adaos.node.storage_settings.v1" or record.get("node_id") != self.node_id:
            raise ValueError("Storage settings identity mismatch")
        return record

    def resolve(self, role):
        if role not in ROLES:
            raise ValueError("Unknown storage role")
        return Path(self.settings()["roles"][role]["active_path"])

    def validate_destination(self, role, path):
        if role not in ROLES:
            raise ValueError("Unknown storage role")
        target = Path(str(path)).expanduser()
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("An absolute storage directory without parent traversal is required")
        if str(target).startswith(("\\\\", "//")):
            raise ValueError("Only local node storage is supported")
        resolved = target.resolve()
        if target.absolute() != resolved:
            raise ValueError("Configure the physical storage root rather than a linked alias")
        if resolved == resolved.parent or (resolved.exists() and not resolved.is_dir()):
            raise ValueError("A dedicated storage directory is required")
        # Protect whole live/source namespaces except this role's existing root.
        current = self.resolve(role).resolve()
        if resolved != current:
            protected = [self.workspace / name for name in ("src", "tests", ".git", ".tmp", ".venv", ".ssh", "docs-development", "scripts", "tools")]
            protected += [self.base / name for name in ("keys", "private", "workspace", "dev", "trials", "data")]
            protected += [self.base]
            for directory in protected:
                directory = directory.resolve()
                if resolved == directory or resolved.is_relative_to(directory) or directory.is_relative_to(resolved):
                    raise ValueError("Storage destination overlaps protected data")
            for other, value in self.settings()["roles"].items():
                for key in ("active_path", "desired_path"):
                    if not value.get(key) or other == role:
                        continue
                    other_path = Path(value[key]).resolve()
                    if resolved == other_path or resolved.is_relative_to(other_path) or other_path.is_relative_to(resolved):
                        raise ValueError("Storage roles must have disjoint roots")
        return resolved

    def plan(self, *, role, desired_path, expected_revision, quota_bytes=None, min_free_bytes=20*1024**3):
        settings = self.settings()
        if type(expected_revision) is not int or settings["revision"] != expected_revision:
            raise ValueError("Storage settings revision changed; refresh before planning")
        if quota_bytes is not None and (type(quota_bytes) is not int or quota_bytes <= 0):
            raise ValueError("Storage quota must be a positive byte count")
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise ValueError("Minimum free space must be a nonnegative byte count")
        target = self.validate_destination(role, desired_path)
        target_volume = volume(target)
        payload = {"schema": "adaos.node.storage_plan.v1", "node_id": self.node_id,
                   "role": role, "expected_revision": expected_revision,
                   "active_path": settings["roles"][role]["active_path"], "desired_path": str(target),
                   "quota_bytes": quota_bytes, "min_free_bytes": min_free_bytes,
                   "volume_id": target_volume["id"], "created_at": now(),
                   "effect": "save_desired_location_only", "moves_data": False, "switches_writers": False}
        plan_id = digest(payload)
        atomic_write_json(self.state / "placement-plans" / f"{plan_id}.json", payload)
        return {**payload, "plan_digest": plan_id, "destination_volume": target_volume,
                "capacity_admitted": target_volume["free_bytes"] >= min_free_bytes}

    def apply(self, plan_digest):
        if len(str(plan_digest)) != 64 or any(c not in "0123456789abcdef" for c in str(plan_digest)):
            raise ValueError("Invalid placement plan digest")
        with mutation_lock(self.state / "storage-settings.lock", timeout_s=2):
            plan = read(self.state / "placement-plans" / f"{plan_digest}.json")
            if not plan or digest(plan) != plan_digest or plan["node_id"] != self.node_id:
                raise ValueError("Exact placement plan is unavailable")
            receipt_path = self.state / "placement-receipts" / f"{plan_digest}.json"
            previous = read(receipt_path)
            if previous:
                return {**previous, "replayed": True}
            settings = self.settings()
            retained = settings.get("last_receipt")
            if retained:
                retained_path = self.state / "placement-receipts" / f"{retained['plan_digest']}.json"
                if not retained_path.exists():
                    atomic_write_json(retained_path, retained)
                if retained["plan_digest"] == plan_digest:
                    return {**retained, "replayed": True}
            if settings["revision"] != plan["expected_revision"]:
                raise ValueError("Storage settings revision changed; plan is stale")
            target = self.validate_destination(plan["role"], plan["desired_path"])
            current_volume = volume(target)
            if current_volume["id"] != plan["volume_id"] or current_volume["free_bytes"] < plan["min_free_bytes"]:
                raise ValueError("Destination volume changed or has insufficient free space")
            updated = json.loads(json.dumps(settings))
            updated["revision"] += 1
            updated["roles"][plan["role"]].update({k: plan[k] for k in ("desired_path", "quota_bytes", "min_free_bytes")})
            updated["updated_at"] = now()
            updated["last_plan_digest"] = plan_digest
            # Intent precedes settings commit. A crash after settings can be reconciled.
            receipt = {"schema": "adaos.node.storage_receipt.v1", "plan_digest": plan_digest,
                       "node_id": self.node_id, "role": plan["role"], "revision": updated["revision"],
                       "active_path": plan["active_path"], "desired_path": str(target),
                       "status": "desired_saved", "moves_data": False, "switches_writers": False,
                       "completed_at": now()}
            updated["last_receipt"] = receipt
            atomic_write_json(self.settings_path, updated)
            atomic_write_json(receipt_path, receipt)
            return {**receipt, "replayed": False}

    def snapshot(self):
        settings = self.settings()
        rows, volumes = [], {}
        for role, value in settings["roles"].items():
            row = {"id": role, "title": ROLES[role][2], "owner": ROLES[role][3], **value}
            row["status"] = "planned" if value.get("desired_path") and value["desired_path"] != value["active_path"] else "current"
            try:
                observed = volume(Path(value["active_path"]))
                row["volume_id"] = observed["id"]
                row["available"] = True
                volumes[observed["id"]] = observed
            except (OSError, ValueError):
                row.update(available=False, status="unavailable")
            if value.get("desired_path"):
                try:
                    desired_volume = volume(Path(value["desired_path"]))
                    volumes[desired_volume["id"]] = desired_volume
                    row["desired_volume_id"] = desired_volume["id"]
                except (OSError, ValueError):
                    row["desired_status"] = "unavailable"
            rows.append(row)
        return {"node_id": self.node_id, "revision": settings["revision"], "locations": rows,
                "volumes": list(volumes.values()), "observed_at": now()}
