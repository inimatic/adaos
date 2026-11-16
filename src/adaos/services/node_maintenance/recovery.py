"""Recovery catalog: archive existence never establishes restore readiness."""
from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
import stat

from .storage import digest, now, read

ADAPTERS = {
    "application_recovery": ("applications.lifecycle", "adaos.services.applications.lifecycle_recovery", "Review the exact Application lifecycle operation"),
    "builder_recovery": ("builder.source_recovery", "adaos.services.builder.source_recovery", "Plan source recovery through Builder"),
    "private_recovery": ("infrastructure.restore", None, "Use the exact encrypted infrastructure restore manifest"),
    "operator_recovery": ("operator.evidence", None, "Classify the retained source/data evidence before choosing a restore adapter"),
}


def register_point(root, *, application_id, candidate_id, release_digest, operation_id, status):
    """Producer-owned identity; no assertion that arbitrary historical restore is supported."""
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    path = Path(root) / ".adaos-recovery.json"
    identity = {"application_id": application_id, "candidate_id": candidate_id,
                "release_digest": release_digest, "adapter": "applications.lifecycle"}
    previous = read(path)
    if previous and any(previous.get(k) != v for k, v in identity.items()):
        raise ValueError("Recovery point identity changed")
    payload = {"schema": "adaos.recovery.point.v1", **identity, "operation_id": operation_id,
               "status": status, "created_at": (previous or {}).get("created_at", now()),
               "updated_at": now(), "restore_scope": "exact_application_lifecycle",
               "integrity": "owner_receipts_required", "restore_rehearsal": "not_run"}
    atomic_write_json(path, payload)
    return payload


def catalog(locations, *, limit=40):
    points, roots = [], []
    # Bound per-root enumeration independently, so a large legacy root cannot
    # hide the remaining owners. No payload or secret-bearing JSON is read.
    for role, (adapter, module, guidance) in ADAPTERS.items():
        root = locations.resolve(role)
        available = bool(find_spec(module)) if module else False
        if role == "private_recovery":
            available = (locations.workspace / "src/adaos/integrations/infra-inimatic/scripts/restore-root-storage.py").is_file()
        root_record = {"role": role, "path": str(root), "adapter": adapter,
                       "adapter_installed": available, "restore_guidance": guidance,
                       "complete": True, "status": "available"}
        try:
            with os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index >= limit:
                        root_record["complete"] = False
                        break
                    info = entry.stat(follow_symlinks=False)
                    linked = stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                    if entry.name.startswith(".") or not stat.S_ISDIR(info.st_mode) or linked:
                        continue
                    point = {"id": digest([role, entry.name]), "role": role, "name": entry.name,
                                   "status": "unclassified", "adapter": adapter, "adapter_installed": available,
                                   "integrity": "not_verified", "restore_rehearsal": "not_run",
                                   "restorable": False, "restore_guidance": guidance,
                                   "observed_at": now(), "modified_at": info.st_mtime}
                    marker = Path(entry.path) / ".adaos-recovery.json"
                    if marker.is_file() and not marker.is_symlink() and marker.stat().st_size <= 65536:
                        try:
                            registered = read(marker)
                            if registered and registered.get("schema") == "adaos.recovery.point.v1" and registered.get("adapter") == adapter:
                                point.update({key: registered.get(key) for key in ("application_id", "candidate_id", "release_digest", "operation_id", "integrity", "restore_rehearsal", "restore_scope")})
                                point["status"] = "registered"
                                point["owner_status"] = registered.get("status")
                        except (ValueError, OSError):
                            point["status"] = "invalid_metadata"
                    points.append(point)
        except FileNotFoundError:
            root_record["status"] = "absent"
        except OSError:
            root_record.update(status="unavailable", complete=False)
        roots.append(root_record)
    return {"points": points, "roots": roots, "observed_at": now(),
            "scope": "bounded_legacy_inventory; no_payload_verification", "complete": all(r["complete"] for r in roots)}
