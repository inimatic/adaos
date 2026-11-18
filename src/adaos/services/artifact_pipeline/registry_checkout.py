"""Publish accepted source through the existing registry using a clean index."""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
import uuid
from contextlib import contextmanager
import shutil

from .publication import PublicationError
from .storage import atomic_write_json
from adaos.services.workspace_registry import load_workspace_registry


@contextmanager
def registry_metadata_checkout(owner, publication, candidate_id: str, *, remote: str, branch: str):
    """Use the existing registry transport for qualified prerelease metadata.

    Canonical Workspace sources belong to Stable. Only semantic registry paths
    may be committed; component, project and runtime registry files stay unchanged.
    """
    from adaos.services.artifact_pipeline.candidates import assert_promotable
    from adaos.services.storage_hygiene import managed_storage_run

    candidate = publication.candidate_store.load(candidate_id)
    plan = publication.get_candidate_release(candidate_id)
    stable = publication.current_stable(plan.release.project_id)
    assert_promotable(candidate, plan.release, stable.release if stable else None)
    # Core-owned source admission follows an exact healthy Candidate, retains
    # failure evidence, and reserves bounded clone space above the 8 GiB floor.
    if shutil.disk_usage(publication.state_root).free < 9 * 1024 ** 3:
        raise PublicationError("registry metadata publication requires 1 GiB above the 8 GiB free-space reserve")
    url = owner.git.remote_url(str(publication.workspace_root), remote)
    if not url:
        raise PublicationError("Source registry remote is unavailable")
    run = publication.state_root / "registry-source-checkouts" / uuid.uuid4().hex
    with managed_storage_run(run, owner="core.registry.prerelease", retention_class="development",
            success_retain_seconds=86400, metadata={"candidate_id": candidate_id,
                "qualified_release_digest": candidate.release_digest, "channel": "prerelease"}):
        target = run / "repo"
        owner.git.ensure_repo(str(target), url, branch=branch)
        owner.git.sparse_add(str(target), "semantic")
        if shutil.disk_usage(target).free < 8 * 1024 ** 3:
            raise PublicationError("registry metadata checkout exhausted the free-space reserve")
        yield target


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
    registry["source_branch"] = branch
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
