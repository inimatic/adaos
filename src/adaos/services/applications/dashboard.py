"""Application-facing relationships over Core release and runtime facts.

No lifecycle writes or authoring-workflow evidence participate in this projection.
Private/Public describe release availability; they do not replace access policy.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from packaging.version import InvalidVersion, Version
from adaos.domain.artifact_release import canonical_payload_digest
from .release_contract import installation_plan_kind


def _release(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _newer_version(candidate, current):
    try:
        return Version(str(candidate)) > Version(str(current))
    except InvalidVersion:
        return False


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
    stable_digest = (lifecycle.get("installation") or {}).get("stable_release_digest") or channels.get("stable")
    prerelease_digest = channels.get("prerelease")
    releases = [_release(model.get(key)) for key in (
        "active_release", "installed_release", "marketplace_release", "local_beta_release", "prerelease_release")]
    retained = list(lifecycle.get("retained_betas") or [])
    releases.extend(_release(row) for row in retained)
    releases.extend(_release(row) for row in model.get("local_beta_releases") or [])
    busy = bool(lifecycle.get("transition") or (lifecycle.get("operation") or {}).get("status") in {"applying", "unknown", "reconciling"})

    def action(eligible, *, reason="ExactReviewedOperationRequired", capability="applications.plan", tool=None, arguments=None):
        return {"eligible": bool(eligible and not busy), "reason": "RuntimeOperationPending" if busy else reason,
                "required_capability": capability, "requires_authorization": True,
                "requires_review": capability != "applications.read", "tool_id": tool,
                "arguments": arguments or {}}

    def select(position, digest, candidate_id=None, eligible=True):
        arguments = {"action": "select_runtime", "target_position_id": position, "release_digest": digest}
        if candidate_id:
            arguments["candidate_id"] = candidate_id
        return action(eligible, tool="applications.lifecycle.plan", arguments=arguments)

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
               "available_at": available_at, "updated_at": None,
               "availability": "ready", "hidden_on_desktop": False, "actions": {}}
        positions.append(row)
        return row

    alpha = revisions.get("alpha")
    if publisher and isinstance(alpha, Mapping):
        row = append("private_alpha", "private", "alpha", alpha, source="development")
        if row is not None:
            row["updated_at"] = alpha.get("updated_at")
            row["active"] = False
            row["availability"] = "development" if alpha.get("status") == "mutable_source" else "unavailable"
            row["actions"] = {name: action(alpha.get("status") == "mutable_source", capability="applications.develop")
                              for name in ("preview", "builder", "dev_tickets")}
            for navigation in row["actions"].values():
                navigation.update(requires_review=False, mode="navigate")

    # The selected Candidate is owned by Core. Historical Builder delivery
    # records may describe another Candidate and must not manufacture this row.
    candidate = lifecycle.get("candidate") or {}
    selected_beta = runtime.get("source") == "local_trial" and selected != stable_digest
    baseline_version = (lifecycle.get('installation') or {}).get('stable_version') or find(stable_digest).get('version')
    beta_fact = next((row for row in retained if row.get("release_digest") == selected), {}) if selected_beta else next(
        (row for row in retained if not baseline_version or _newer_version(row.get('version'), baseline_version)), {})
    if publisher and (selected_beta or beta_fact):
        beta = find(selected) if selected_beta else beta_fact
        row = append("private_beta", "private", "beta", beta, source="local_trial", available_at=beta_fact.get("available_at"))
        if row is not None:
            row["candidate_id"] = candidate.get("candidate_id") if selected_beta else beta_fact.get("candidate_id")
            row["expires_at"] = runtime.get("expires_at") if selected_beta else None
            row["admission_kind"] = runtime.get("admission_kind") if selected_beta else "installed_local_beta"
            row["availability"] = ("ready" if runtime.get("admission") in {None, "admitted", "expiring"}
                                   else "expired" if runtime.get("admission") == "expired" else "unavailable") if selected_beta else beta_fact.get("availability", "unavailable")
            if selected_beta:
                row["actions"] = {name: dict((lifecycle.get("actions") or {}).get(command) or {"eligible": False})
                                  for name, command in (("open", "open"), ("finalize", "promote_stable"),
                                                       ("remove_beta", "deactivate_beta"))}
                for name in ("renew_beta", "restore_stable"):
                    row["actions"][name] = dict((lifecycle.get("actions") or {}).get(name) or {"eligible": False})
                for name, command in (("finalize", "promote_stable"), ("remove_beta", "deactivate_beta"),
                                      ("renew_beta", "renew_beta"), ("restore_stable", "restore_stable")):
                    row["actions"][name].update(tool_id="applications.lifecycle.plan", requires_review=True,
                        arguments={"action": command, "candidate_id": row["candidate_id"]})
            else:
                row["actions"] = {"open": select("private_beta", row["release_digest"], row["candidate_id"], row["availability"] == "ready"),
                    "finalize": action(False, reason="SelectBetaBeforeFinalize", capability="applications.publish"),
                    "remove_beta": action(False, reason="ReviewRetainedTrialInRuntimePlacement")}
            newer = next((item for item in retained if item.get("availability") == "ready"
                          and _newer_version(item.get("version"), row["version"])), None)
            if newer:
                row["update_version"] = newer["version"]
                row["update_available_at"] = newer.get("available_at")
                row["actions"]["update"] = select("private_beta", newer["release_digest"], newer["candidate_id"])

    stable = find(stable_digest)
    if publisher and stable:
        row = append("private_stable", "private", "stable", stable, source="stable_installation",
                     available_at=stable.get("published_at") or (model.get("installation") or {}).get("updated_at"))
        if row is not None:
            row["hidden_on_desktop"] = bool(selected_beta)
            row["actions"]["open"] = dict((lifecycle.get("actions") or {}).get("open") or {"eligible": False}) if row["active"] else select(
                "private_stable", stable_digest, eligible=(lifecycle.get("installation") or {}).get("stable_baseline_present", False))

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
    if public_stable and prerelease and prerelease_digest != public_stable.get("release_digest"):
        append("public_beta", "public", "beta", prerelease,
               available_at=prerelease.get("published_at"), source="prerelease")
    if public_stable:
        append("public_stable", "public", "stable", public_stable,
               available_at=public_stable.get("published_at") or receipt.get("published_at"), source="marketplace")

    exact_stable = lifecycle.get("stable_candidate") or {}
    publishable = bool(exact_stable.get("release_digest") == stable_digest and not exact_stable.get("publication_blocker"))
    for row in positions:
        digest = row.get("release_digest")
        if row["id"] == "private_stable":
            if not public_stable:
                row["actions"]["publish_initial_stable"] = action(publishable, capability="applications.publish",
                    reason="ExactFirstStableBootstrapRequired", tool="applications.development.publish_to_registry",
                    arguments={"release_digest": digest})
            else:
                row["actions"]["publish_beta"] = action(publishable and digest != public_stable.get("release_digest") and digest != prerelease_digest,
                    capability="applications.publish", reason="AlreadyPublicStable" if digest == public_stable.get("release_digest") else "AlreadyPublicBeta" if digest == prerelease_digest else "ExactPrivateStablePublicationRequired",
                    tool="applications.development.publish_prerelease", arguments={"candidate_id": exact_stable.get("candidate_id"),
                        "expected_prerelease_digest": prerelease_digest})
        if row["scope"] == "public":
            row["actions"]["open"] = dict((lifecycle.get("actions") or {}).get("open") or {"eligible": False}) if row["active"] else action(
                True, reason="ExplicitPrereleaseOptInRequired" if row["stage"] == "beta" else "ExactPublicReleaseInstallReviewRequired",
                tool="applications.plan", arguments={"kind": installation_plan_kind(model),
                    "release_digest": digest, "update_track": "prerelease" if row["stage"] == "beta" else "stable"})
            row["actions"]["select_release"] = action(True, capability="applications.read", tool="applications.list_releases")
            if row["stage"] == "beta" and publisher:
                row["actions"]["promote_public_stable"] = action(publishable and digest == stable_digest,
                    capability="applications.publish", reason="ExactAcceptedPublicationDigestRequired",
                    tool="applications.development.publish_to_registry", arguments={"release_digest": digest})

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
        if "open" in row["actions"] and row["actions"]["open"].get("eligible"):
            row["actions"]["open"]["mode"] = "open" if row.get("release_digest") == selected else "switch_and_open"
        elif "open" in row["actions"]:
            row["actions"]["open"].pop("mode", None)
    transition = lifecycle.get("transition") or {}
    result = {"schema": "adaos.application.dashboard.v1", "application_id": application.get("application_id"),
            "lifecycle_revision": lifecycle.get("revision"), "observed_at": lifecycle.get("observed_at"),
            "freshness": lifecycle.get("freshness", "observed"),
            "publisher_local": publisher, "active_position_id": active_id, "positions": positions,
            "groups": [{"id": scope, "position_ids": [p["id"] for p in positions if p["scope"] == scope]}
                       for scope in ("private", "public") if any(p["scope"] == scope for p in positions)],
            "operation": {"action": "finalize" if str(transition.get("operation_id") or "").startswith("application-stable:") else "prepare_beta" if str(transition.get("operation_id") or "").startswith("application-beta:") else "select_runtime", "status": transition.get("status"),
                          "target_version": transition.get("target_version"),
                          "source_version": transition.get("source_version") or runtime.get("version"),
                          "stable_version": stable.get("version"),
                          "operation_id": transition.get("operation_id")} if transition else None,
            "read_only": True}
    result["revision"] = canonical_payload_digest({key: value for key, value in result.items() if key != "observed_at"})
    return result
