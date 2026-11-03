"""Core-owned, read-only operational facts; authoring workflow is not authority.

This projection joins existing records. It never repairs, selects, publishes,
renews a lease, hashes entire package trees, or executes application code.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

from .runtime_channel import ApplicationRuntimeChannel
from .runtime_transition import ApplicationRuntimeTransition
from .store import ApplicationStore
from .transition_observation import observe_transition
from .publication_readiness import candidate_publication_blocker


LIFECYCLE_SCHEMA = "adaos.application.lifecycle.v1"
EXPIRING_SECONDS = 48 * 60 * 60


def timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Lifecycle timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _action(eligible: bool, reason: str, capability: str = "applications.apply") -> dict[str, Any]:
    return {"eligible": bool(eligible), "reason": reason,
            "required_capability": capability, "requires_authorization": True}


def workspace_release_kind(store, application_id: str, digest: str) -> str:
    """The legacy stable_installation source means Workspace, not stability."""
    channels = store.get_channels(application_id).get("channels", {}) if hasattr(store, "get_channels") else {}
    if channels.get("stable") == digest:
        return "stable"
    if channels.get("prerelease") == digest:
        return "prerelease"
    release = store.get_release(application_id, digest)
    return "stable" if getattr(release, "lifecycle", "stable") == "stable" else "prerelease"


def observe_runtime_admission(state: Path, selected: Any, *, now: datetime | None = None,
                              store=None) -> dict[str, Any]:
    """One cheap admission projection shared by inventory, detail and clock observer."""
    fact = _observe_selected_runtime_admission(state, selected, now=now, store=store)
    if selected is not None and ApplicationRuntimeChannel(state, selected.application_id).pending_transitions():
        fact.update(admission="recovery_required", reason="RuntimeTransitionPending")
    return fact


def _observe_selected_runtime_admission(state: Path, selected: Any, *, now: datetime | None = None,
                                        store=None) -> dict[str, Any]:
    observed = now or datetime.now(timezone.utc)
    if selected is None:
        return {"admission": "not_selected", "reason": "NoSelectedRuntime", "expires_at": None,
                "remaining_seconds": None, "admission_kind": None, "trial_expires_at": None}
    if selected.source != "local_trial":
        kind = workspace_release_kind(store or ApplicationStore(state), selected.application_id, selected.release_digest)
        return {"admission": "admitted", "reason": "StableInstallation" if kind == "stable" else "InstalledPrerelease", "expires_at": None,
                "remaining_seconds": None, "admission_kind": "stable" if kind == "stable" else "installed_prerelease", "trial_expires_at": None}
    from .local_beta_admission import read_local_beta_admission
    candidate_id = selected.runtime_root_ref.removeprefix("trial:")
    activation = TrialActivationStore(Path(state) / "artifact_pipeline/trial-activations").load(candidate_id) or {}
    identity = activation.get("candidate_ref") or {}
    expiry = timestamp(activation.get("expires_at"))
    remaining = int((expiry - observed).total_seconds()) if expiry else None
    try:
        installed = read_local_beta_admission(state, candidate_id, selected.release_digest, activation)
    except (OSError, ValueError, KeyError):
        return {"admission": "unavailable", "reason": "InstalledLocalBetaAdmissionInvalid",
                "expires_at": None, "remaining_seconds": None, "admission_kind": "invalid_local_beta",
                "trial_expires_at": activation.get("expires_at")}
    admission, reason = "admitted", "TrialActive"
    if (activation.get("status") not in {"active", "completed"}
            or identity.get("candidate_id") != candidate_id
            or identity.get("release_digest") != selected.release_digest):
        admission, reason = "unavailable", "TrialAdmissionUnavailable"
    elif installed:
        reason = "InstalledLocalBeta"
    elif remaining is not None and remaining <= 0:
        admission, reason = "expired", "TrialExpired"
    elif remaining is not None and remaining <= EXPIRING_SECONDS:
        admission, reason = "expiring", "TrialExpiring"
    return {"admission": admission, "reason": reason,
            "expires_at": activation.get("expires_at") if not installed else None,
            "trial_expires_at": activation.get("expires_at"),
            "remaining_seconds": max(0, remaining) if remaining is not None and not installed else None,
            "admission_kind": "installed_local_beta" if installed else "temporary_trial"}


class ApplicationLifecycleService:
    """Node authority shared by SDK/MCP, Builder, Applications and Management."""

    def __init__(self, owner: Any, *, store: ApplicationStore | None = None):
        self.owner = getattr(owner, "authority_context", None) or owner
        self.state = Path(self.owner.paths.state_dir())
        self.store = store or ApplicationStore(self.state)

    def for_source(self, source_kind: str, source_id: str, *, webspace_id: str = "desktop") -> dict:
        """Resolve declared Application ownership; never guess from display names."""
        if source_kind not in {"project", "skill", "scenario"} or not source_id:
            raise ValueError("An explicit authoring source kind/id is required")
        matches = []
        # Read each collection once, not one installation path for every
        # registered Application (including hundreds of inactive definitions).
        selections = self.store.list_runtime_selections() if source_kind != "project" else ()
        installations = {item.application_id: item for item in self.store.list_installations()} if source_kind != "project" else {}
        for app in self.store.list_applications():
            if source_kind == "project":
                if app.legacy_project_id == source_id:
                    matches.append(app.application_id)
                continue
            ref = f"{source_kind}:{source_id}"
            if any(e.get("presentation_ref") == ref for e in app.entrypoints):
                matches.append(app.application_id)
                continue
            releases = {s.release_digest for s in selections if s.application_id == app.application_id}
            baseline = installations.get(app.application_id)
            if baseline is not None and baseline.status == "active":
                releases.add(baseline.installed_release_digest)
            for digest in releases:
                if self.store.release_owns_source(app.application_id, digest, ref):
                    matches.append(app.application_id)
                    break
        matches = sorted(set(matches))
        if len(matches) != 1:
            return {"schema": "adaos.application.lifecycle_source.v1", "available": False,
                    "reason": "ApplicationNotRegistered" if not matches else "ApplicationOwnershipAmbiguous",
                    "summary": "No unique Core Application owner is registered for this source.",
                    "source_ref": f"{source_kind}:{source_id}"}
        return self.snapshot(matches[0], webspace_id=webspace_id)

    def operations(self, application_id: str, *, limit: int = 50) -> dict:
        """Correlated, bounded, redacted history over existing journals."""
        self.store.get_application(application_id)
        records = []
        for op in self.store.list_operations(application_id):
            records.append({"operation_id": op.operation_id, "source": "application", "action": op.plan.get("action", op.kind),
                "status": op.status, "summary": op.plan.get("summary") or op.plan.get("review_summary") or op.kind,
                "updated_at": op.updated_at, "recovery_reason": "ExactCoreRecoveryRequired" if op.recovery_reason else None,
                "plan_digest": op.plan_digest,
                "recoverable": op.status in {"applying", "unknown", "reconciling"} and (
                    op.plan.get("schema") == "adaos.application.lifecycle_plan.v1" or op.kind == "remove"),
                "correlated_operation_id": op.result.get("runtime_transition_operation_id")})
        from .development import ApplicationDevelopmentCoordinator, RECOVERABLE_PUBLICATION_ACTIONS
        for op in ApplicationDevelopmentCoordinator(self.state).list(application_id):
            records.append({"operation_id": op["operation_id"], "source": "authoring", "action": op["action"],
                "status": op["status"], "summary": "Authoring operation: " + op["action"], "updated_at": op["updated_at"],
                "plan_digest": op["intent_digest"],
                "recoverable": op["status"] in {"applying", "unknown"} and (
                    op["action"] in RECOVERABLE_PUBLICATION_ACTIONS or
                    op["action"] == "decide_trial" and "expected_selections" in op["intent"]),
                "recovery_reason": "ExactCoreRecoveryRequired" if op.get("recovery_reason") else None})
        channel = ApplicationRuntimeChannel(self.state, application_id)
        if channel.path.is_file():
            with channel._connection() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='transitions'").fetchone():
                    for op_id, raw in db.execute("SELECT operation_id,document FROM transitions"):
                        op = json.loads(raw)
                        progress = observe_transition(op)
                        records.append({"operation_id": op_id, "source": "runtime", "action": "runtime_transition",
                            "status": progress["status"], "progress": progress,
                            "summary": "Runtime journal: " + str(op.get("running_step") or "checkpoint"),
                            "updated_at": progress["updated_at"] or str((op.get("intent") or {}).get("target", {}).get("updated_at") or ""),
                            "recovery_reason": None if op.get("completed") or op.get("aborted") else "RuntimeFenced",
                            "correlated_operation_id": ((op.get("intent") or {}).get("publication_review") or {}).get("review_id"),
                            "recovered_from_operation_id": ((op.get("intent") or {}).get("publication_review") or {}).get("previous_operation_id")})
        for path in (self.state / "applications/distribution").glob("*.json"):
            op = json.loads(path.read_text(encoding="utf-8"))
            if op.get("application_id") == application_id:
                records.append({"operation_id": op.get("operation_id") or "distribution:" + path.stem,
                    "source": "publication", "action": "publication", "status": op.get("status", "unknown"),
                    "summary": "Immutable distribution journal", "updated_at": op.get("updated_at", ""),
                    "recovery_reason": None if op.get("status") == "completed" else "ReviewDistributionJournal"})
        records.sort(key=lambda r: (r.get("updated_at") or "", r["operation_id"]), reverse=True)
        return {"schema": "adaos.application.lifecycle_operation_page.v1", "application_id": application_id,
                "operations": records[:max(1, min(int(limit), 200))], "total": len(records), "private_content_included": False}

    def snapshot(self, application_id: str, *, webspace_id: str = "desktop",
                 runtime_revisions: Mapping[str, Any] | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        application = self.store.get_application(application_id)
        selections = self.store.list_application_runtime_selections(application_id)
        selected = next((s for s in selections if s.webspace_id == webspace_id), None)
        # A channel is Application-wide. The requested UI need not be one of
        # its current placements to observe the selected operational edition.
        selected = selected or next(iter(selections), None)
        try:
            baseline = self.store.get_installation(application_id)
        except FileNotFoundError:
            baseline = None
        baseline_active = baseline is not None and baseline.status == "active"
        baseline_kind = workspace_release_kind(self.store, application_id, baseline.installed_release_digest) if baseline_active else None
        stable_baseline = baseline_active and baseline_kind == "stable"
        is_beta = bool(selected and selected.source == "local_trial")
        digest = selected.release_digest if selected else (
            baseline.installed_release_digest if baseline_active else None)
        release = self.store.get_release(application_id, digest) if digest else None
        candidate_id = (selected.runtime_root_ref.removeprefix("trial:") if is_beta
                        else getattr(release, "accepted_candidate_id", None))
        candidate = None
        candidate_reason = "NoCandidate"
        if candidate_id:
            try:
                candidate = CandidateStore(self.state / "artifact_pipeline/candidates").load(candidate_id)
                if (candidate.project_id != application.legacy_project_id
                        or candidate.release_digest != digest):
                    raise ValueError("Candidate identity differs from selected Application")
                candidate_reason = "ExactCandidate"
            except (OSError, ValueError, KeyError):
                candidate = None
                candidate_reason = "CandidateUnavailable"
        activation = (TrialActivationStore(self.state / "artifact_pipeline/trial-activations").load(candidate_id)
                      if is_beta and candidate_id else None) or {}
        from .local_beta_admission import read_local_beta_admission

        try:
            installed_beta = (read_local_beta_admission(self.state, candidate_id, digest, activation)
                              if is_beta and candidate_id else None)
        except (OSError, ValueError, KeyError):
            installed_beta = None  # Observation stays available for data-only recovery.
        admission_fact = observe_runtime_admission(self.state, selected, now=observed, store=self.store)
        admission, reason = admission_fact["admission"], admission_fact["reason"]
        if digest:
            admission, reason = "admitted", ("StableInstallation" if baseline_kind == "stable" else "InstalledPrerelease") if not is_beta else "TrialActive"
        if is_beta:
            admission, reason = admission_fact["admission"], admission_fact["reason"]
        channel = ApplicationRuntimeChannel(self.state, application_id)
        transition = next(iter(channel.pending_transitions()), None)
        if transition:
            admission, reason = "recovery_required", "RuntimeTransitionPending"
        if candidate_id:
            runner = ApplicationRuntimeTransition(channel)
            for prefix in ("application-stable", "application-beta"):
                record = runner.get(f"{prefix}:{application_id}:{candidate_id}")
                if record and not record.get("completed") and not record.get("aborted"):
                    transition = record
                    admission, reason = "recovery_required", "RuntimeTransitionPending"
                    break
        integrity = dict(runtime_revisions or {})
        if integrity.get("status") in {"unavailable", "drift"} and admission in {"admitted", "expiring"}:
            admission, reason = "unavailable", "RuntimeIntegrityUnavailable"
        operations = self.store.list_operations(application_id)
        operation = next(iter(operations), None)
        from .materialization import request_path, _materialization_identity
        path = request_path(self.state, application_id)
        materialization_record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        materialization_status = materialization_record.get("status", "not_requested")
        if materialization_record:
            rooms = sorted(set(materialization_record.get("webspace_ids", [])) | {s.webspace_id for s in selections})
            expected_projection = _materialization_identity(application_id, selections, rooms,
                materialization_record.get("priority_webspace_ids", []))
            if materialization_record.get("request_digest") != expected_projection:
                materialization_status = "stale"
        materialization = {"status": materialization_status,
                           "observed_at": materialization_record.get("observed_at"),
                           "attempts": materialization_record.get("attempts", 0),
                           "priority_webspace_ids": materialization_record.get("priority_webspace_ids", []),
                           "webspaces": materialization_record.get("observations", []),
                           "selected_webspace_status": next(("ready" if item.get("ok") else "pending"
                               for item in materialization_record.get("observations", [])
                               if item.get("webspace_id") == webspace_id), "not_observed")}
        if digest and materialization["selected_webspace_status"] != "ready":
            from .materialization import live_projection_ready
            if live_projection_ready(application_id, digest, webspace_id,
                    since=materialization_record.get("requested_at")):
                materialization["selected_webspace_status"] = "ready"
        operation_projection = None
        if operation:
            pending = (bool(operation.result.get("runtime_refresh", {}).get("pending"))
                       and materialization["status"] != "completed")
            operation_projection = {
                "operation_id": operation.operation_id, "kind": operation.kind,
                "status": operation.status, "revision": operation.revision,
                "phase": "committed_refresh_pending" if operation.status == "succeeded" and pending
                         else operation.status,
                "recovery_reason": operation.recovery_reason,
                "updated_at": operation.updated_at,
            }
        protected = bool((application.protection or {}).get("system_application"))
        removable = bool((application.protection or {}).get("active_installation_removable", not protected))
        exact = bool(candidate and candidate_reason == "ExactCandidate")
        usable = admission in {"admitted", "expiring"}
        reviewable_beta = (is_beta and exact and candidate.status in {"trial", "accepted"}
            and transition is None and admission in {"expired", "expiring", "admitted"}
            and (activation.get("health_evidence") or {}).get("status") == "passed"
            and not (operation and operation.status in {"unknown", "applying", "reconciling"}))
        publication_blocker = candidate_publication_blocker(candidate, self.state) if candidate else None
        actions = {
            "open": _action(usable, reason, "applications.read"),
            "renew_beta": _action(reviewable_beta and not installed_beta,
                "ExactTemporaryBetaRequired", "applications.apply"),
            "promote_stable": _action(reviewable_beta and publication_blocker is None,
                publication_blocker["error"] if publication_blocker else "ExactHealthyCandidateRequired", "applications.publish"),
            "retain_local_beta": _action(is_beta and exact and candidate.status == "accepted" and usable and not installed_beta,
                "ExactAcceptedTrialRequired", "applications.apply"),
            "deactivate_beta": _action(is_beta and transition is None,
                "SelectedBetaRequired" if not is_beta else "ReviewRuntimeAndDataDisposition"),
            "restore_stable": _action(is_beta and stable_baseline and transition is None,
                "VerifiedStableBaselineRequired"),
            "uninstall_beta": _action(is_beta and not baseline_active and removable and transition is None,
                "BetaOnlyInstallationRequired"),
            "uninstall": _action(bool(digest) and removable and transition is None,
                "ProtectedApplication" if not removable else "ReviewUninstallPlan"),
            "recover": _action(bool(transition and observe_transition(transition)["status"] == "recovery_required") or bool(operation and operation.status in {"unknown", "reconciling"}),
                "ExactPendingOperationRequired", "applications.recover"),
            "cancel_plan": _action(bool(operation and operation.status == "planned"),
                "UnstartedPlanRequired", "applications.plan"),
            "accept_candidate": _action(is_beta and exact and candidate.status == "trial" and transition is None,
                "ExactTrialCandidateRequired", "applications.develop"),
            "reject_candidate": _action(is_beta and exact and candidate.status == "trial" and transition is None,
                "ExactTrialCandidateRequired", "applications.develop"),
        }
        refs = {"application_revision": getattr(application, "revision", 0),
                "publisher_ref": getattr(application, "publisher_ref", None),
                "selection_revisions": {s.webspace_id: s.revision for s in selections},
                "installation_revision": baseline.revision if baseline else 0,
                "release_digest": digest, "candidate_digest": candidate.digest if candidate else None,
                "candidate_status": candidate.status if candidate else None,
                "publication_blocker": publication_blocker,
                "admission_reason": reason,
                "pending_transition": transition.get("fingerprint") if transition else None,
                "transition_progress": observe_transition(transition) if transition else None,
                "activation_updated_at": activation.get("updated_at"),
                "activation_status": activation.get("status"), "expires_at": activation.get("expires_at"),
                "local_beta_admission": (installed_beta or {}).get("receipt_digest")}
        return {
            "schema": LIFECYCLE_SCHEMA, "application_id": application_id,
            "observed_at": observed.isoformat(), "read_only": True,
            "revision": canonical_payload_digest(refs), "freshness": "observed",
            "installation": {"present": bool(digest), "beta_only": is_beta and not baseline_active,
                             "stable_baseline_present": stable_baseline,
                             "stable_release_digest": baseline.installed_release_digest if stable_baseline else None,
                             "stable_version": self.store.get_release(application_id, baseline.installed_release_digest).project_release.version if stable_baseline else None,
                             "workspace_baseline_present": baseline_active,
                             "workspace_release_digest": baseline.installed_release_digest if baseline_active else None},
            "runtime": {"selected": bool(digest), "source": selected.source if selected else
                        "stable_installation" if baseline_active else None,
                        "release_digest": digest, "version": release.project_release.version if release else None,
                        "runtime_root_ref": selected.runtime_root_ref if selected else "workspace" if baseline_active else None,
                        "webspace_ids": sorted(s.webspace_id for s in selections),
                        "selections": [s.to_dict() for s in selections],
                        "admission": admission, "reason": reason,
                        "admission_kind": admission_fact["admission_kind"] if selected else (
                            "stable" if baseline_kind == "stable" else "installed_prerelease") if baseline_active else None,
                        "trial_expires_at": admission_fact["trial_expires_at"],
                        "expires_at": admission_fact["expires_at"],
                        "remaining_seconds": admission_fact["remaining_seconds"],
                        "integrity": integrity.get("status", "not_checked")},
            "candidate": {"candidate_id": candidate_id, "candidate_digest": candidate.digest if candidate else None,
                          "package_digest": candidate.package_digest if candidate else None,
                          "release_digest": digest, "status": candidate.status if candidate else "unknown",
                          "accepted": bool(candidate and candidate.status == "accepted"), "reason": candidate_reason,
                          "publication_blocker": publication_blocker},
            "data": {"disposition": "retained", "transition_pending": transition is not None,
                     "operation_id": transition.get("operation_id") if transition else None,
                     "recovery_candidate_id": str(((transition or {}).get("intent", {}).get("target") or {}).get(
                         "runtime_root_ref") or (f"trial:{candidate_id}" if candidate_id else "")).removeprefix("trial:")},
            "operation": operation_projection, "actions": actions,
            "transition": observe_transition(transition) if transition else None,
            "materialization": materialization,
            "summary": f"{('Beta' if is_beta or baseline_kind == 'prerelease' else 'Stable') if digest else 'Not installed'}: "
                       f"{release.project_release.version if release else '—'}; {reason}",
        }
