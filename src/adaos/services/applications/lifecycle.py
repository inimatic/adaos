"""Core-owned, read-only operational facts; authoring workflow is not authority.

This projection joins existing records. It never repairs, selects, publishes,
renews a lease, hashes entire package trees, or executes application code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

from .runtime_channel import ApplicationRuntimeChannel
from .runtime_transition import ApplicationRuntimeTransition
from .store import ApplicationStore


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


class ApplicationLifecycleService:
    """Node authority shared by SDK/MCP, Builder, Applications and Management."""

    def __init__(self, owner: Any, *, store: ApplicationStore | None = None):
        self.owner = getattr(owner, "authority_context", None) or owner
        self.state = Path(self.owner.paths.state_dir())
        self.store = store or ApplicationStore(self.state)

    def snapshot(self, application_id: str, *, webspace_id: str = "desktop",
                 runtime_revisions: Mapping[str, Any] | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        application = self.store.get_application(application_id)
        selections = tuple(s for s in self.store.list_runtime_selections()
                           if s.application_id == application_id)
        selected = next((s for s in selections if s.webspace_id == webspace_id), None)
        # A channel is Application-wide. The requested UI need not be one of
        # its current placements to observe the selected operational edition.
        selected = selected or next(iter(selections), None)
        try:
            baseline = self.store.get_installation(application_id)
        except FileNotFoundError:
            baseline = None
        baseline_active = baseline is not None and baseline.status == "active"
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
        expiry = timestamp(activation.get("expires_at"))
        remaining = int((expiry - observed).total_seconds()) if expiry else None
        from .local_beta_admission import read_local_beta_admission

        installed_beta = (read_local_beta_admission(self.state, candidate_id, digest, activation)
                          if is_beta and candidate_id else None)
        admission = "not_selected"
        reason = "NoSelectedRuntime"
        if digest:
            admission, reason = "admitted", "StableInstallation" if not is_beta else "TrialActive"
        if is_beta:
            identity = activation.get("candidate_ref") or {}
            if (activation.get("status") not in {"active", "completed"}
                    or identity.get("candidate_id") != candidate_id
                    or identity.get("release_digest") != digest):
                admission, reason = "unavailable", "TrialAdmissionUnavailable"
            elif installed_beta:
                admission, reason = "admitted", "InstalledLocalBeta"
            elif remaining is not None and remaining <= 0:
                admission, reason = "expired", "TrialExpired"
            elif remaining is not None and remaining <= EXPIRING_SECONDS:
                admission, reason = "expiring", "TrialExpiring"
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
        operation_projection = None
        if operation:
            pending = bool(operation.result.get("runtime_refresh", {}).get("pending"))
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
        actions = {
            "open": _action(usable, reason, "applications.read"),
            "retain_local_beta": _action(is_beta and exact and candidate.status == "accepted" and usable and not installed_beta,
                "ExactAcceptedTrialRequired", "applications.apply"),
            "deactivate_beta": _action(is_beta and transition is None,
                "SelectedBetaRequired" if not is_beta else "ReviewRuntimeAndDataDisposition"),
            "uninstall": _action(bool(digest) and removable and transition is None,
                "ProtectedApplication" if not removable else "ReviewUninstallPlan"),
            "recover": _action(transition is not None or bool(operation and operation.status in {"unknown", "reconciling"}),
                "ExactPendingOperationRequired", "applications.recover"),
            "cancel_plan": _action(bool(operation and operation.status == "planned"),
                "UnstartedPlanRequired", "applications.plan"),
        }
        refs = {"selection_revisions": {s.webspace_id: s.revision for s in selections},
                "installation_revision": baseline.revision if baseline else 0,
                "release_digest": digest, "candidate_digest": candidate.digest if candidate else None,
                "activation_updated_at": activation.get("updated_at"),
                "activation_status": activation.get("status"), "expires_at": activation.get("expires_at"),
                "local_beta_admission": (installed_beta or {}).get("receipt_digest")}
        return {
            "schema": LIFECYCLE_SCHEMA, "application_id": application_id,
            "observed_at": observed.isoformat(), "read_only": True,
            "revision": canonical_payload_digest(refs), "freshness": "observed",
            "installation": {"present": bool(digest), "beta_only": is_beta and not baseline_active,
                             "stable_baseline_present": baseline_active,
                             "stable_release_digest": baseline.installed_release_digest if baseline_active else None},
            "runtime": {"selected": bool(digest), "source": selected.source if selected else
                        "stable_installation" if baseline_active else None,
                        "release_digest": digest, "version": release.project_release.version if release else None,
                        "runtime_root_ref": selected.runtime_root_ref if selected else "workspace" if baseline_active else None,
                        "webspace_ids": sorted(s.webspace_id for s in selections),
                        "admission": admission, "reason": reason,
                        "admission_kind": "installed_local_beta" if installed_beta else "temporary_trial" if is_beta else "stable",
                        "trial_expires_at": activation.get("expires_at") if is_beta else None,
                        "expires_at": activation.get("expires_at") if is_beta and not installed_beta else None,
                        "remaining_seconds": max(0, remaining) if remaining is not None and not installed_beta else None,
                        "integrity": integrity.get("status", "not_checked")},
            "candidate": {"candidate_id": candidate_id, "candidate_digest": candidate.digest if candidate else None,
                          "package_digest": candidate.package_digest if candidate else None,
                          "release_digest": digest, "status": candidate.status if candidate else "unknown",
                          "accepted": bool(candidate and candidate.status == "accepted"), "reason": candidate_reason},
            "data": {"disposition": "retained", "transition_pending": transition is not None,
                     "operation_id": transition.get("operation_id") if transition else None},
            "operation": operation_projection, "actions": actions,
            "summary": f"{('Beta' if is_beta else 'Stable') if digest else 'Not installed'}: "
                       f"{release.project_release.version if release else '—'}; {reason}",
        }
