"""Publish accepted source through the existing registry using a clean index."""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
import uuid

from .publication import PublicationError
from .storage import atomic_write_json
from adaos.services.workspace_registry import load_workspace_registry


def prepare_registry_checkout(owner, publication, candidate_id: str, *, remote: str, branch: str) -> Path:
    """Keep live files, staged work and unresolved merges outside publication.

    Only CAS packages and the retained, verified development source projection
    enter this checkout. It targets the same configured registry and branch;
    failures retain their checkout for diagnosis and ordinary exact retry.
    """
    publication.verify_promoted_workspace_source(candidate_id)
    plan = publication.get_candidate_release(candidate_id)
    operation = publication.load_promotion(candidate_id) or {}
    projection = (operation.get("receipts") or {}).get("development_sources_prepared")
    if not isinstance(projection, dict):
        raise PublicationError("Published source has no retained development projection")
    url = owner.git.remote_url(str(publication.workspace_root), remote)
    if not url:
        raise PublicationError("Source registry remote is unavailable")
    target = publication.state_root / "registry-source-checkouts" / uuid.uuid4().hex
    owner.git.ensure_repo(str(target), url, branch=branch)
    paths = ["semantic", f"projects/{plan.release.project_id}", *[
        package.materialization_path or f"{package.kind}s/{package.artifact_id}" for package in plan.packages]]
    for path in paths:
        owner.git.sparse_add(str(target), path)
    projected = copy.copy(publication)
    projected.workspace_root = target.resolve()
    for package in plan.packages:
        verified = publication.package_store.materialize(package.digest, projected._workspace_package_target(package))
        if verified.ref != package:
            raise PublicationError("Source publication package identity changed")
    projected._project_development_sources(projection, plan=plan)
    projected._verify_development_source_projection(projection, plan=plan)
    previous_registry = load_workspace_registry(target)
    projected._record_workspace_projection(plan)
    registry = load_workspace_registry(target)
    stamp = operation["completed_at"]
    identities = {(p.kind + "s", p.artifact_id) for p in plan.packages} | {("projects", plan.release.project_id)}
    for collection in ("skills", "scenarios", "projects"):
        for entry in registry.get(collection, []):
            if (collection, entry.get("name")) in identities:
                entry["updated_at"] = stamp
    prior_stamp = previous_registry.get("updated_at") or stamp
    registry["updated_at"] = max((prior_stamp, stamp), key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
    atomic_write_json(target / "registry.json", registry)
    projected.verify_promoted_workspace_source(candidate_id)
    return projected.workspace_root
