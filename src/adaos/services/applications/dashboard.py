"""Application-facing relationships over Core release and runtime facts.

No lifecycle writes or authoring-workflow evidence participate in this projection.
Private/Public describe release availability; they do not replace access policy.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _release(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def project_application_dashboard(model: Mapping[str, Any], *, local_subnet_ref: str,
                                  public_stable: Mapping[str, Any] | None = None) -> dict:
    application = model.get("application") or {}
    lifecycle = model.get("lifecycle") or {}
    runtime = lifecycle.get("runtime") or {}
    revisions = model.get("runtime_revisions") or {}
    local = model.get("local_development") or {}
    channels = model.get("channels") or {}
    publisher = bool(local_subnet_ref and application.get("publisher_ref") == local_subnet_ref)
    selected = runtime.get("release_digest")
    stable_digest = channels.get("stable")
    prerelease_digest = channels.get("prerelease")
    releases = [_release(model.get(key)) for key in (
        "active_release", "installed_release", "marketplace_release", "local_beta_release", "prerelease_release")]

    def find(digest):
        return next((r for r in releases if digest and r.get("release_digest") == digest), {})

    positions = []

    def append(identity, scope, stage, release, *, available_at=None, source=None):
        digest = release.get("release_digest")
        version = release.get("version") or (release.get("project_release") or {}).get("version")
        if not version:
            return None
        active = bool(digest and digest == selected and stage != "alpha")
        row = {"id": identity, "scope": scope, "stage": stage, "version": version,
               "release_digest": digest, "source": source, "active": active,
               "available_at": available_at, "actions": {}}
        positions.append(row)
        return row

    alpha = revisions.get("alpha")
    if isinstance(alpha, Mapping):
        row = append("private_alpha", "private", "alpha", alpha, source="development")
        if row is not None:
            row["updated_at"] = alpha.get("updated_at")
            row["active"] = False
            row["actions"] = {name: {"eligible": alpha.get("status") == "mutable_source"}
                              for name in ("preview", "builder", "dev_tickets")}

    # The selected Candidate is owned by Core. Historical Builder delivery
    # records may describe another Candidate and must not manufacture this row.
    candidate = lifecycle.get("candidate") or {}
    if publisher and runtime.get("source") == "local_trial" and selected != stable_digest:
        beta = find(selected)
        row = append("private_beta", "private", "beta", beta, source="local_trial")
        if row is not None:
            row["candidate_id"] = candidate.get("candidate_id")
            row["expires_at"] = runtime.get("expires_at")
            row["admission_kind"] = runtime.get("admission_kind")
            row["actions"] = {name: dict((lifecycle.get("actions") or {}).get(action) or {"eligible": False})
                              for name, action in (("open", "open"), ("finalize", "promote_stable"),
                                                   ("remove_beta", "deactivate_beta"))}

    stable = find(stable_digest)
    if publisher and stable:
        row = append("private_stable", "private", "stable", stable, source="stable_installation")
        if row is not None:
            row["actions"]["open"] = dict((lifecycle.get("actions") or {}).get("open") or {"eligible": False}) if row["active"] else {"eligible": False, "reason": "ExplicitRuntimeSelectionRequired"}

    receipt = local.get("source_registry") or {}
    catalog_receipt = receipt.get("application_catalog_publication") or {}
    if public_stable is None:
        if not publisher and application.get("visibility") == "public":
            public_stable = stable
        elif (receipt.get("status") == "published" and receipt.get("release_digest") == stable_digest
              and catalog_receipt.get("release_digest") == stable_digest):
            public_stable = stable
    prerelease = find(prerelease_digest)
    # Local acceptance and public distribution are independent. A publisher
    # can offer the same exact Private Stable digest as Public Beta while the
    # Marketplace still serves the previously published Public Stable.
    if prerelease and prerelease_digest != (public_stable or {}).get("release_digest"):
        append("public_beta", "public", "beta", prerelease,
               available_at=prerelease.get("published_at"), source="prerelease")
    if public_stable:
        append("public_stable", "public", "stable", public_stable,
               available_at=public_stable.get("published_at") or receipt.get("published_at"), source="marketplace")

    # A public and private position can name the same artifact. Select its
    # factual runtime source once rather than painting both as active.
    active_id = None
    preferred = "private_beta" if runtime.get("source") == "local_trial" else (
        "private_stable" if publisher and selected == stable_digest else
        "public_beta" if selected and selected == prerelease_digest and selected != stable_digest else
        "private_stable" if publisher else "public_stable")
    for row in positions:
        row["active"] = bool(row["active"] and row["id"] == preferred)
        if row["active"]:
            active_id = row["id"]
    transition = lifecycle.get("transition") or {}
    return {"schema": "adaos.application.dashboard.v1", "application_id": application.get("application_id"),
            "revision": lifecycle.get("revision"), "observed_at": lifecycle.get("observed_at"),
            "publisher_local": publisher, "active_position_id": active_id, "positions": positions,
            "groups": [{"id": scope, "position_ids": [p["id"] for p in positions if p["scope"] == scope]}
                       for scope in ("private", "public") if any(p["scope"] == scope for p in positions)],
            "operation": {"action": "finalize" if str(transition.get("operation_id") or "").startswith("application-stable:") else "select_runtime", "status": transition.get("status"),
                          "target_version": runtime.get("version"),
                          "stable_version": stable.get("version"),
                          "operation_id": transition.get("operation_id")} if transition else None,
            "read_only": True}
