"""Reviewed Core local lifecycle commands over existing operation/channel stores.

Core owns exact reviewed intents. Stable acceptance delegates to the existing
verified publication/data adoption pipeline after explicit user approval.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from adaos.domain.application import ApplicationOperation, RuntimeSelection, utc_now
from adaos.domain.artifact_release import WorkspaceLock, canonical_payload_digest
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.mutation_lock import MutationLockTimeout
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore, trial_workspace_root

from .lifecycle import ApplicationLifecycleService
from .local_beta_admission import admission_path
from .publication_attempts import PublicationReviewRequired, observed_recovered_adoption, reviewed_publication
from .runtime import get_application_service
from .runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict, same_runtime_selections
from .runtime_transition import ApplicationRuntimeTransition
from .trial_runtime import NativeTrialRuntime


PLAN_SCHEMA = "adaos.application.lifecycle_plan.v1"
_ACTIONS = {"renew_beta", "promote_stable", "retain_local_beta", "deactivate_beta", "restore_stable", "uninstall", "uninstall_beta", "delete_trial", "recover_runtime", "select_runtime"}
_TERMINAL_JOBS = {"completed", "succeeded", "failed", "cancelled", "canceled", "interrupted",
                  "refused", "out_of_scope", "incomplete", "invalid_output"}


def assert_idle_jobs(state: Path, application_id: str, runtime_root: Path | None = None, *, component_refs=None) -> dict:
    """Read Core-owned job metadata only; ambiguity must not authorize removal."""
    consumers = {application_id, "application:" + application_id}
    if component_refs is not None:
        consumers.update(component_refs)
    elif runtime_root and (runtime_root / ".adaos/workspace.lock.json").is_file():
        lock = WorkspaceLock.from_mapping(json.loads((runtime_root / ".adaos/workspace.lock.json").read_text(encoding="utf-8")))
        consumers.update("skill:" + item.artifact_id for item in lock.components if item.kind == "skill")
    for path in (Path(state) / "ai").glob("*/ai.sqlite3"):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.25)) as db:
            rows = db.execute("SELECT consumer,status FROM attempts WHERE status IN ('queued','running')")
            for consumer, _status in rows:
                if str(consumer) in consumers:
                    raise RuntimeChannelConflict("ApplicationHasActiveCoreJobs")
    if runtime_root:
        for service in ("content-generation", "image-generation"):
            for path in (runtime_root / ".runtime/state" / service).glob("*/*.json"):
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status") not in _TERMINAL_JOBS:
                    raise RuntimeChannelConflict("ApplicationHasUnresolvedGenerationJobs")
    return {"ok": True, "reason": "CoreJobsIdle"}


class ApplicationLifecycleOperations:
    def __init__(self, owner: Any):
        self.owner = getattr(owner, "authority_context", None) or owner
        self.state = Path(self.owner.paths.state_dir()).resolve()
        self.workspace = Path(self.owner.paths.workspace_dir()).resolve()
        self.service = get_application_service(self.state)
        self.store = self.service.store
        self.observer = ApplicationLifecycleService(self.owner, store=self.store)

    def _authorize(self, actor: str, subnet: str, capability: str, required: str) -> None:
        from .service import _require_authority
        from adaos.services.policy.skill_capabilities import require_skill_capability

        _require_authority(actor_ref=actor, subnet_ref=subnet, capability=capability, required_capability=required)
        config = self.owner.config
        local = str(config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id)
        local = local if local.startswith("subnet:") else f"subnet:{local}"
        if subnet.lower() != local.lower():
            raise PermissionError("Lifecycle operation subnet differs from node authority")
        current = self.owner.skill_ctx.get() if getattr(self.owner, "skill_ctx", None) else None
        if str(getattr(current, "name", "") or "").strip():
            require_skill_capability(self.owner, required)

    def _trial(self, application_id: str, candidate_id: str):
        app = self.store.get_application(application_id)
        candidate = CandidateStore(self.state / "artifact_pipeline/candidates").load(candidate_id)
        if candidate.project_id != app.legacy_project_id:
            raise ValueError("Candidate belongs to another Application")
        activation = TrialActivationStore(self.state / "artifact_pipeline/trial-activations").load(candidate_id)
        if not activation or (activation.get("candidate_ref") or {}).get("release_digest") != candidate.release_digest:
            raise ValueError("Exact Trial activation is required")
        return candidate, activation

    def _inactive_beta_retention_evidence(self, application_id, candidate, activation) -> dict:
        """Verify a completed Trial before installing it as an inactive Beta."""

        if candidate.status != "accepted" or (activation.get("health_evidence") or {}).get("status") != "passed":
            raise RuntimeChannelConflict("ExactHealthyAcceptedTrialRequired")
        runtime = NativeTrialRuntime.resolve(self.owner, candidate.candidate_id, candidate.release_digest)
        for package in runtime.packages:
            runtime.verified_source(package)
        transition_id = f"application-beta:{application_id}:{candidate.candidate_id}"
        transition = ApplicationRuntimeTransition(
            ApplicationRuntimeChannel(self.state, application_id)
        ).get(transition_id)
        target = ((transition or {}).get("intent") or {}).get("target") or {}
        proof = (activation.get("safety_evidence") or {}).get("data_transition") or {}
        binding_path = runtime.root / ".adaos/data-transition.json"
        if not binding_path.is_file():
            raise RuntimeChannelConflict("ReviewedDataTransitionBindingRequired")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        expected_target = {
            "application_id": application_id,
            "source": "local_trial",
            "release_digest": candidate.release_digest,
            "runtime_root_ref": f"trial:{candidate.candidate_id}",
        }
        expected_binding = {
            "application_id": application_id,
            "candidate_id": candidate.candidate_id,
            "release_digest": candidate.release_digest,
        }
        if (
            not transition
            or not transition.get("completed")
            or transition.get("aborted")
            or any(target.get(key) != value for key, value in expected_target.items())
            or proof.get("operation_id") != transition_id
            or proof.get("contract_digest") != (transition.get("intent") or {}).get("contract_digest")
            or proof.get("completed") is not True
            or any(binding.get(key) != value for key, value in expected_binding.items())
        ):
            raise RuntimeChannelConflict("CompletedTrialRetentionEvidenceRequired")
        return {
            "transition_operation_id": transition_id,
            "transition_fingerprint": transition.get("fingerprint"),
            "contract_digest": proof["contract_digest"],
            "binding_digest": canonical_payload_digest(binding),
        }

    def trials(self, application_id: str, *, limit: int = 50, offset: int = 0,
               include_storage: bool = False) -> dict:
        """Explicit inventory; expensive storage observation is opt-in and bounded."""
        import os
        app = self.store.get_application(application_id)
        selected = {s.runtime_root_ref for s in self.store.list_runtime_selections()}
        records = []
        activations = TrialActivationStore(self.state / "artifact_pipeline/trial-activations").list_all()
        for activation in activations:
            identity = activation.get("candidate_ref") or {}
            candidate_id = str(identity.get("candidate_id") or "")
            if not candidate_id:
                continue
            try:
                candidate = CandidateStore(self.state / "artifact_pipeline/candidates").load(candidate_id)
            except (OSError, ValueError):
                if (activation.get("release_ref") or {}).get("project_id") != app.legacy_project_id:
                    continue
                candidate = None
            if candidate is not None and candidate.project_id != app.legacy_project_id:
                continue
            root = trial_workspace_root(self.workspace, candidate_id)
            status = "deleted" if activation.get("lifecycle_deletion") else str(activation.get("status") or "unknown")
            expiry = activation.get("expires_at")
            if status in {"active", "completed"} and expiry and datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
                status = "expired"
            is_selected = f"trial:{candidate_id}" in selected
            from .local_beta_admission import read_local_beta_admission
            installed_beta = read_local_beta_admission(self.state, candidate_id, str(identity.get("release_digest") or ""), activation)
            if installed_beta and is_selected:
                status = "installed_beta"
            record = {"candidate_id": candidate_id, "release_digest": identity.get("release_digest"),
                "version": candidate.version if candidate else None, "status": status,
                "candidate_status": candidate.status if candidate else "unknown", "selected": is_selected,
                "expires_at": expiry, "data_policy": "retained", "recoverable": bool(activation.get("lifecycle_deletion")),
                "deletion_review_available": bool(candidate) and not is_selected and root.is_dir(),
                "deletion_eligible": None,
                "deletion_reason": "SelectedRuntime" if is_selected else "ReviewReferenceAndJobChecksRequired",
                "storage_bytes": None, "storage_complete": False, "storage_reason": "NotRequested"}
            records.append(record)
        records.sort(key=lambda item: (not item["selected"], str(item["candidate_id"])))
        start, size = max(0, int(offset)), max(1, min(int(limit), 100))
        page = records[start:start+size]
        references = self._reference_index([record["candidate_id"] for record in page], activations=activations)
        for record in page:
            root = trial_workspace_root(self.workspace, record["candidate_id"])
            refs = references[record["candidate_id"]]
            record["references"] = refs[:20]
            record["reference_count"] = len(refs)
            record["deletion_review_available"] = record["deletion_review_available"] and not refs
            record["deletion_reason"] = "TrialReferenced" if refs else "ReviewJobChecksRequired"
            if record["deletion_review_available"]:
                try:
                    assert_idle_jobs(self.state, application_id, root)
                except (RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
                    record["deletion_review_available"] = False
                    record["deletion_reason"] = str(exc) if isinstance(exc, RuntimeChannelConflict) else "JobMetadataUnavailable"
            if include_storage and root.is_dir():
                total, count, complete = 0, 0, True
                for current, dirs, files in os.walk(root, followlinks=False):
                    count += 1
                    if count >= 1024 or len(dirs) + len(files) > 1024:
                        complete = False
                        break
                    if any((Path(current) / name).is_symlink() or getattr(Path(current) / name, "is_junction", lambda: False)() for name in dirs):
                        complete = False
                        break
                    for name in files:
                        path = Path(current) / name
                        if path.is_symlink():
                            complete = False
                            break
                        total += path.stat().st_size
                        count += 1
                        if count >= 1024:
                            complete = False
                            break
                    if not complete:
                        break
                record.update(storage_bytes=total if complete else None, storage_complete=complete,
                              storage_reason="Observed" if complete else "InventoryBoundExceeded")
        return {"schema": "adaos.application.trial_inventory.v1", "application_id": application_id,
                "trials": page, "total": len(records), "offset": start,
                "next_offset": start+size if start+size < len(records) else None,
                "data_policy": "retained", "private_content_included": False}

    def _references(self, application_id: str, candidate_id: str, *, ignore_operation: str | None = None) -> list[str]:
        return self._reference_index([candidate_id], ignore_operation=ignore_operation)[candidate_id]

    def _reference_index(self, candidate_ids, *, activations=None, ignore_operation=None) -> dict[str, list[str]]:
        """One metadata scan per inventory page; mutations always take a fresh scan."""
        import re
        refs = {candidate_id: [] for candidate_id in candidate_ids}
        targets = {f"trial:{candidate_id}": candidate_id for candidate_id in refs}
        if not refs:
            return refs
        for selection in self.store.list_runtime_selections():
            candidate_id = targets.get(selection.runtime_root_ref)
            if candidate_id:
                refs[candidate_id].append(f"selected:{selection.webspace_id}")
        # Pending transitions can reference an unselected target or rollback source.
        root = self.state / "applications/runtime_channels"
        for path in root.glob("*.sqlite3"):
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.25)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='transitions'").fetchone():
                    continue
                for operation_id, raw in db.execute("SELECT operation_id,document FROM transitions WHERE completed=0"):
                    record = json.loads(raw)
                    if not record.get("aborted"):
                        intent = json.dumps(record.get("intent", {}))
                        for root_ref, candidate_id in targets.items():
                            if root_ref in intent:
                                refs[candidate_id].append(f"pending_transition:{operation_id}")
        store = TrialActivationStore(self.state / "artifact_pipeline/trial-activations")
        activations = store.list_all() if activations is None else activations
        by_id = {}
        for other in activations:
            other_id = str((other.get("candidate_ref") or {}).get("candidate_id") or "")
            if not other_id:
                continue
            by_id[other_id] = other
            other_root = trial_workspace_root(self.workspace, other_id)
            marker = other.get("lifecycle_deletion")
            if marker:
                operation_id = str(marker.get("operation_id") or "")
                if not re.fullmatch(r"applifecycle\.[0-9a-f]{32}", operation_id):
                    for candidate_id in refs:
                        if candidate_id != other_id:
                            refs[candidate_id].append(f"unresolved_archive:{other_id}")
                    continue
                other_root = self.state / "artifact_pipeline/trial-deletions" / operation_id / "runtime"
                if not other_root.is_dir() or other_root.absolute() != other_root.resolve():
                    for candidate_id in refs:
                        if candidate_id != other_id:
                            refs[candidate_id].append(f"unresolved_archive:{other_id}")
                    continue
            binding = other_root / ".adaos/data-transition.json"
            if binding.is_file():
                if binding.absolute() != binding.resolve():
                    raise RuntimeChannelConflict("UnlinkedDataReferenceMetadataRequired")
                source = json.loads(binding.read_text(encoding="utf-8")).get("beta_source") or {}
                candidate_id = targets.get(source.get("runtime_root_ref"))
                if candidate_id and candidate_id != other_id:
                    refs[candidate_id].append(f"retained_data_source:{other_id}")
        for op in self.store.list_operations():
            if op.operation_id != ignore_operation and op.status in {"planned", "applying", "unknown", "reconciling"}:
                intent = json.dumps(op.plan)
                for candidate_id in refs:
                    if candidate_id in intent:
                        refs[candidate_id].append(f"operation:{op.operation_id}")
        for candidate_id in refs:
            activation = by_id.get(candidate_id) or store.load(candidate_id) or {}
            if activation.get("retention_holds") or activation.get("legal_hold"):
                refs[candidate_id].append("explicit_retention_hold")
        return {candidate_id: sorted(set(values)) for candidate_id, values in refs.items()}

    def plan(self, application_id: str, action: str, *, actor_ref: str, subnet_ref: str,
             capability: str, idempotency_key: str, expected_revision: str,
             webspace_id: str = "desktop", candidate_id: str | None = None,
             acknowledge_write_loss: bool = False, target_position_id: str | None = None,
             release_digest: str | None = None, _confirmed: bool = False,
             _expected_release_digest: str | None = None) -> dict:
        arguments = dict(actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability,
            idempotency_key=idempotency_key, expected_revision=expected_revision, webspace_id=webspace_id,
            candidate_id=candidate_id, acknowledge_write_loss=acknowledge_write_loss,
            target_position_id=target_position_id, release_digest=release_digest,
            _confirmed=_confirmed, _expected_release_digest=_expected_release_digest)
        identity = hashlib.sha256(f"{application_id}:{idempotency_key}".encode()).hexdigest()[:32]
        try:
            self.store.get_operation(f"applifecycle.{identity}")
        except FileNotFoundError:
            pass
        else:
            # Exact replay is read-only and must remain available while the
            # existing worker holds the Application execution lock.
            return self._plan(application_id, action, **arguments)
        token = hashlib.sha256(application_id.encode()).hexdigest()
        try:
            with mutation_lock(self.store.root / "operation-leases" / f"{token}.lock", timeout_s=.25):
                return self._plan(application_id, action, **arguments)
        except MutationLockTimeout as exc:
            raise RuntimeChannelConflict("RuntimeOperationPending") from exc

    def _assert_no_conflicting_operation(self, application_id: str, *, exclude: str = "") -> None:
        operations = self.store.list_operations(application_id)
        latest = next((op for op in operations if op.status != "planned"), None)
        if any(op.operation_id != exclude and (op.status in {"applying", "reconciling"}
               or op.status == "unknown" and (op is latest or (op.result.get("confirmation") or {}).get("confirmed")))
               for op in operations):
            raise RuntimeChannelConflict("RuntimeOperationPending")

    def _plan(self, application_id: str, action: str, *, actor_ref: str, subnet_ref: str,
             capability: str, idempotency_key: str, expected_revision: str,
             webspace_id: str = "desktop", candidate_id: str | None = None,
             acknowledge_write_loss: bool = False, target_position_id: str | None = None,
             release_digest: str | None = None, _confirmed: bool = False,
             _expected_release_digest: str | None = None) -> dict:
        self._authorize(actor_ref, subnet_ref, capability,
                        "applications.publish" if _confirmed else "applications.plan")
        if _confirmed and (action != "promote_stable" or not candidate_id or not _expected_release_digest):
            raise ValueError("Confirmed Finalize requires the exact reviewed Candidate and release")
        if action not in _ACTIONS or not idempotency_key:
            raise ValueError("Known lifecycle action and idempotency key are required")
        if action != "select_runtime" and (target_position_id is not None or release_digest is not None):
            raise ValueError("Runtime target fields require the select_runtime action")
        if action in {"renew_beta", "promote_stable"} and not actor_ref.startswith("user:"):
            raise PermissionError("BetaChangeRequiresUserDecision")
        identity = hashlib.sha256(f"{application_id}:{idempotency_key}".encode()).hexdigest()[:32]
        try:
            prior = self.store.get_operation(f"applifecycle.{identity}")
        except FileNotFoundError:
            prior = None
        if prior:
            replay_action = "uninstall_beta" if action == "uninstall" and prior.plan.get("action") == "uninstall_beta" else action
            expected = {"schema": PLAN_SCHEMA, "action": replay_action, "application_id": application_id,
                        "expected_lifecycle_revision": expected_revision, "webspace_id": webspace_id,
                        "actor_ref": actor_ref, "subnet_ref": subnet_ref,
                        "acknowledge_write_loss": acknowledge_write_loss}
            if (any(prior.plan.get(k) != v for k, v in expected.items())
                    or candidate_id and prior.plan.get("candidate_id") != candidate_id
                    or action == "select_runtime" and (prior.plan.get("target_position_id") != target_position_id
                        or release_digest and prior.plan.get("release_digest") != release_digest)):
                raise RuntimeChannelConflict("IdempotencyKeyNamesDifferentLifecycleIntent")
            if _confirmed and (prior.plan.get("release_digest") != _expected_release_digest
                    or not (prior.result.get("confirmation") or {}).get("confirmed")):
                raise RuntimeChannelConflict("IdempotencyKeyNamesDifferentLifecycleIntent")
            return prior.to_dict()
        if action != "recover_runtime":
            self._assert_no_conflicting_operation(application_id)
        snapshot = self.observer.snapshot(application_id, webspace_id=webspace_id)
        if snapshot["revision"] != expected_revision:
            raise RuntimeChannelConflict("LifecycleRevisionChanged")
        if _confirmed and snapshot["runtime"].get("release_digest") != _expected_release_digest:
            raise RuntimeChannelConflict("ExactReviewedReleaseRequired")
        if action == "select_runtime":
            if acknowledge_write_loss:
                raise ValueError("Runtime selection retains each edition; it does not acknowledge data loss")
            from .runtime_selection_review import plan_selection
            return plan_selection(self, snapshot, application_id=application_id, position=target_position_id,
                release_digest=release_digest, candidate_id=candidate_id, webspace_id=webspace_id,
                actor_ref=actor_ref, subnet_ref=subnet_ref, idempotency_key=idempotency_key,
                operation_id=f"applifecycle.{identity}")
        if action == "uninstall":
            if snapshot["installation"]["workspace_baseline_present"]:
                baseline = self.store.get_installation(application_id)
                return self.service.plan_operation(application_id, "remove", expected_revision=baseline.revision,
                    actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability,
                    idempotency_key=idempotency_key, data_policy="retain").to_dict()
            action = "uninstall_beta"
        selected = snapshot["candidate"].get("candidate_id")
        target_candidate = candidate_id or selected
        if not target_candidate:
            raise ValueError("Exact Candidate is required")
        candidate, activation = self._trial(application_id, target_candidate)
        if action == "delete_trial":
            references = self._references(application_id, target_candidate)
            if references:
                raise RuntimeChannelConflict("TrialReferenced:" + ",".join(references))
        elif action != "recover_runtime":
            inactive_beta_retention = (
                action == "retain_local_beta"
                and selected != target_candidate
                and snapshot["runtime"].get("source") == "stable_installation"
            )
            if not inactive_beta_retention and (
                selected != target_candidate
                or snapshot["runtime"].get("source") != "local_trial"
            ):
                raise RuntimeChannelConflict("ExactSelectedTrialRequired")
            references = []
        else:
            references = []
            runner = ApplicationRuntimeTransition(ApplicationRuntimeChannel(self.state, application_id))
            finalizing = runner.get(f"application-stable:{application_id}:{target_candidate}")
            pending = runner.get(
                f"application-beta:{application_id}:{target_candidate}")
            no_binding = not (trial_workspace_root(self.workspace, target_candidate) / ".adaos/data-transition.json").exists()
            cancel_finalize = bool(finalizing and not finalizing.get("completed") and not finalizing.get("aborted"))
            if not cancel_finalize and ((not pending and not no_binding) or pending and (pending.get("completed") or pending.get("aborted"))):
                raise RuntimeChannelConflict("ExactPendingPreparationRequired")
        if action in {"deactivate_beta", "restore_stable"} and not snapshot["actions"][action]["eligible"]:
            raise RuntimeChannelConflict("RecoverPendingTransitionBeforeDeactivation")
        receipt_path = admission_path(self.state, target_candidate)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
        retention_evidence = None
        if action == "retain_local_beta":
            if inactive_beta_retention:
                retention_evidence = self._inactive_beta_retention_evidence(
                    application_id, candidate, activation
                )
                if receipt.get("status") == "active":
                    raise RuntimeChannelConflict("LocalBetaAdmissionAlreadyExists")
            elif not snapshot["actions"][action]["eligible"]:
                raise RuntimeChannelConflict("ExactHealthyAcceptedTrialRequired")
        if action in {"renew_beta", "promote_stable"}:
            if not snapshot["actions"][action]["eligible"]:
                raise RuntimeChannelConflict(snapshot["actions"][action]["reason"])
            if self.store.get_application(application_id).publisher_ref != subnet_ref:
                raise PermissionError("OnlyPublisherMayChangeLocalBeta")
            self._verify_reviewed_beta(candidate, activation)
        if action == "retain_local_beta" and self.store.get_application(application_id).publisher_ref != subnet_ref:
            raise PermissionError("OnlyPublisherMayInstallLocalBeta")
        if action == "uninstall_beta":
            if snapshot["installation"]["workspace_baseline_present"]:
                raise ValueError("Whole-Application uninstall with Stable must use the aggregate removal plan")
            if not snapshot["actions"]["uninstall"]["eligible"]:
                raise PermissionError("ProtectedApplication")
        if action in {"deactivate_beta", "restore_stable"} and not acknowledge_write_loss:
            raise ValueError("Acknowledge that retained Beta writes are not adopted into the restored source")
        root = trial_workspace_root(self.workspace, target_candidate)
        if action not in {"retain_local_beta", "renew_beta"}:
            assert_idle_jobs(self.state, application_id, root)
        if action in {"deactivate_beta", "restore_stable"}:
            binding_path = root / ".adaos/data-transition.json"
            if not binding_path.is_file():
                raise RuntimeChannelConflict("ReviewedDataTransitionBindingRequired")
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            source = binding.get("beta_source")
            if source and action != "restore_stable":
                NativeTrialRuntime.resolve(self.owner, source["runtime_root_ref"].removeprefix("trial:"), source["release_digest"])
            elif binding.get("stable_release_digest"):
                baseline = self.store.get_installation(application_id)
                lock = WorkspaceLock.from_mapping(json.loads((self.workspace / ".adaos/workspace.lock.json").read_text(encoding="utf-8")))
                if (baseline.status != "active" or baseline.installed_release_digest != binding["stable_release_digest"]
                        or not any(s.project_id == candidate.project_id and s.release_digest == baseline.installed_release_digest for s in lock.slots)):
                    raise RuntimeChannelConflict("StableSourceChanged")
        plan = {"schema": PLAN_SCHEMA, "action": action, "application_id": application_id,
                "source_version": snapshot['runtime'].get('version'),
                "target_version": candidate.version if action in {'promote_stable', 'renew_beta', 'retain_local_beta'} else None,
                "data_disposition": ('Adopt reviewed Beta data into Stable; retain recovery artifacts.' if action == 'promote_stable'
                    else 'Retain Beta data without adopting its writes into the restored edition.' if action in {'deactivate_beta', 'restore_stable'}
                    else 'Retain existing data under the reviewed operation.'),
                "candidate_id": target_candidate, "candidate_digest": candidate.digest,
                "release_digest": candidate.release_digest, "package_digest": candidate.package_digest,
                "expected_lifecycle_revision": expected_revision, "webspace_id": webspace_id,
                "affected_webspace_ids": snapshot["runtime"]["webspace_ids"],
                "expected_selections": snapshot["runtime"]["selections"],
                "expected_admission_receipt_digest": receipt.get("receipt_digest"),
                "stable_release_digest": snapshot["installation"].get("stable_release_digest"),
                "actor_ref": actor_ref, "subnet_ref": subnet_ref, "data_policy": "retain",
                "required_apply_capability": "applications.publish" if action == "promote_stable" else "applications.recover" if action == "recover_runtime" else "applications.apply",
                "acknowledge_write_loss": acknowledge_write_loss, "references": references,
                "trial_binding_digest": canonical_payload_digest(activation.get("runtime_binding") or {}),
                **({"retention_mode": "inactive_completed_trial",
                    "retention_evidence": retention_evidence} if retention_evidence else
                   {"retention_mode": "selected_trial"} if action == "retain_local_beta" else {}),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                "summary": f"{action}: {application_id}; version {candidate.version}; " + (
                    "adopt reviewed Beta data into Stable" if action == "promote_stable" else "isolated data retained"),
                **({"previous_expires_at": activation.get("expires_at"),
                    "renewed_expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                    "activation_digest": canonical_payload_digest(activation)} if action == "renew_beta" else {})}
        if action == "recover_runtime" and cancel_finalize:
            plan["recovery_mode"] = "cancel_finalize_before_effects"
            plan["summary"] = f"Cancel unfinished Finalize: {application_id}; retain the selected Beta and unchanged Stable"
        if action == "promote_stable":
            plan["recovered_adoption_digest"] = observed_recovered_adoption(self.state, application_id, target_candidate)
        digest = canonical_payload_digest(plan)
        op = ApplicationOperation(operation_id=f"applifecycle.{identity}", application_id=application_id,
            kind="remove" if action == "uninstall_beta" else "reconcile", status="applying" if _confirmed else "planned",
            created_at=datetime.now(timezone.utc).isoformat(),
            actor_ref=actor_ref, subnet_ref=subnet_ref,
            plan_digest=digest, idempotency_key=idempotency_key, expected_revision=0, revision=1, plan=plan,
            result={"confirmation": {"confirmed": True, "plan_digest": digest,
                "actor_ref": actor_ref, "subnet_ref": subnet_ref},
                "execution": {"attempts": 0, "status": "queued"}} if _confirmed else {})
        return self.store.put_operation(op).to_dict()

    def finalize(self, application_id: str, *, candidate_id: str, release_digest: str,
                 expected_revision: str, actor_ref: str, subnet_ref: str,
                 capability: str, idempotency_key: str, webspace_id: str = "desktop") -> dict:
        """Persist one user-confirmed intent before acknowledging it to the UI.

        Use the existing reviewed plan and operation journal. There is no
        unconfirmed-plan/confirmation gap and no browser-owned execution job.
        """
        identity = hashlib.sha256(f"{application_id}:{idempotency_key}".encode()).hexdigest()
        # Concurrent transport replay must observe the first persisted plan,
        # including its original deadline/digest, rather than mint another one.
        with mutation_lock(self.store.root / "operation-leases" / f"confirmation-{identity}.lock", timeout_s=30):
            return self.plan(application_id, "promote_stable", actor_ref=actor_ref,
                subnet_ref=subnet_ref, capability=capability, idempotency_key=idempotency_key,
                expected_revision=expected_revision, webspace_id=webspace_id,
                candidate_id=candidate_id, _confirmed=True, _expected_release_digest=release_digest)

    def apply(self, operation_id: str, *, plan_digest: str, actor_ref: str, subnet_ref: str,
              capability: str, recover: bool = False) -> dict:
        operation = self.store.get_operation(operation_id)
        retained_recovery = recover and operation.status in {"applying", "unknown", "reconciling", "succeeded"}
        required = ("applications.recover" if retained_recovery and capability == "applications.recover"
                    else "applications.publish" if operation.plan.get("action") == "promote_stable"
                    else "applications.recover" if recover or operation.plan.get("action") == "recover_runtime"
                    else "applications.apply")
        self._authorize(actor_ref, subnet_ref, capability, required)
        if (operation.plan.get("schema") != PLAN_SCHEMA or operation.plan_digest != plan_digest
                or operation.actor_ref != actor_ref or operation.subnet_ref != subnet_ref):
            raise PermissionError("Exact reviewed lifecycle operation authority is required")
        token = hashlib.sha256(operation.application_id.encode()).hexdigest()
        with mutation_lock(self.store.root / "operation-leases" / f"{token}.lock", timeout_s=.25):
            operation = self.store.get_operation(operation_id)
            if operation.status == "succeeded":
                return operation.to_dict()
            if operation.plan.get("action") != "recover_runtime":
                self._assert_no_conflicting_operation(operation.application_id, exclude=operation.operation_id)
            if operation.status != "planned" and not (recover and operation.status in {"applying", "unknown", "reconciling"}):
                raise RuntimeChannelConflict("OperationRequiresExactRecoveryOrNewPlan")
            if operation.status == "planned":
                plan = operation.plan
                if datetime.fromisoformat(plan["expires_at"]) <= datetime.now(timezone.utc):
                    raise RuntimeChannelConflict("ReviewedPlanExpired")
                observed = self.observer.snapshot(operation.application_id, webspace_id=plan["webspace_id"])
                if observed["revision"] != plan["expected_lifecycle_revision"]:
                    raise RuntimeChannelConflict("LifecycleRevisionChanged")
            result = dict(operation.result)
            if operation.plan.get("action") == "promote_stable":
                result["confirmation"] = {"confirmed": True, "plan_digest": plan_digest,
                    "actor_ref": actor_ref, "subnet_ref": subnet_ref}
                result["execution"] = {"attempts": int((result.get("execution") or {}).get("attempts", 0)) + 1,
                                       "status": "running"}
            applying = self.service._transition_operation(operation, "reconciling" if recover else "applying", result=result)
            try:
                result = self._effect(applying)
            except PublicationReviewRequired as exc:
                return self.service._transition_operation(applying, "failed", result={**applying.result, **exc.result()}).to_dict()
            except Exception as exc:
                if applying.plan.get("action") == "select_runtime":
                    try:
                        retained = ApplicationRuntimeTransition(ApplicationRuntimeChannel(self.state, applying.application_id)).get(
                            f"application-select:{applying.operation_id}")
                    except (OSError, ValueError, RuntimeError, sqlite3.Error):
                        retained = {"unconfirmed": True}
                    if retained is None or retained.get("aborted"):
                        return self.service._transition_operation(applying, "failed", result={"ok": False,
                            "error": {"code": "RuntimeSelectionPreconditionFailed", "type": type(exc).__name__}}).to_dict()
                result = dict(applying.result)
                if operation.plan.get("action") == "promote_stable":
                    from .finalization_runtime import finalization_failure, retryable_finalization_error
                    attempts = int((result.get("execution") or {}).get("attempts", 0))
                    retryable = retryable_finalization_error(exc) and attempts < 5
                    result["execution"] = {"attempts": attempts,
                        "status": "retrying" if retryable else "blocked",
                        "failure": finalization_failure(exc)}
                    if retryable:
                        result["execution"]["retry_at"] = (
                            datetime.now(timezone.utc) + timedelta(seconds=min(15 * 2 ** attempts, 120))).isoformat()
                    if result['execution']['failure'].get('code') == 'SharedSkillConflict':
                        retained = ApplicationRuntimeTransition(ApplicationRuntimeChannel(self.state, applying.application_id)).get(
                            f"application-stable:{applying.application_id}:{applying.plan['candidate_id']}")
                        if retained is None:
                            # Deterministic preflight rejection has no data/runtime
                            # effects to reconcile and must not fence Beta forever.
                            return self.service._transition_operation(applying, 'failed', result=result).to_dict()
                self.service._transition_operation(applying, "unknown", result=result, recovery_reason=type(exc).__name__)
                raise
            if applying.plan.get("action") == "promote_stable":
                result = {**applying.result, **result,
                    "execution": {"attempts": applying.result["execution"]["attempts"], "status": "completed"}}
            return self.service._transition_operation(applying, "succeeded", result=result).to_dict()

    def _effect(self, operation: ApplicationOperation) -> dict:
        plan = operation.plan
        if plan["action"] == "select_runtime":
            from .runtime_selection_review import select_reviewed_runtime
            return select_reviewed_runtime(self, operation)
        application_id, candidate_id = operation.application_id, plan["candidate_id"]
        candidate, activation = self._trial(application_id, candidate_id)
        if (candidate.digest != plan["candidate_digest"] or candidate.release_digest != plan["release_digest"]
                or canonical_payload_digest(activation.get("runtime_binding") or {}) != plan["trial_binding_digest"]):
            raise RuntimeChannelConflict("ExactCandidateOrBindingChanged")
        root = trial_workspace_root(self.workspace, candidate_id)
        action = plan["action"]
        if action == "renew_beta":
            previous = activation.get("lifecycle_renewal") or {}
            if previous.get("operation_id") == operation.operation_id:
                if activation.get("expires_at") != plan["renewed_expires_at"]:
                    raise RuntimeChannelConflict("RenewedBetaLeaseChanged")
                return {"ok": True, "expires_at": activation["expires_at"], "status": "beta_renewed"}
            if canonical_payload_digest(activation) != plan["activation_digest"]:
                raise RuntimeChannelConflict("ReviewedBetaActivationChanged")
            self._verify_reviewed_beta(candidate, activation)
            channel = ApplicationRuntimeChannel(self.state, application_id)
            expected = tuple(RuntimeSelection.from_mapping(s) for s in plan["expected_selections"])
            with channel._connection() as db:
                channel._initialize(db, expected)
                db.execute("BEGIN IMMEDIATE")
                channel._assert_available(db)
                actual = channel._decode(db.execute("SELECT document FROM channel WHERE id=1").fetchone()[0])
                if not same_runtime_selections(actual, expected):
                    raise RuntimeChannelConflict("SelectedTrialChanged")
                TrialActivationStore(self.state / "artifact_pipeline/trial-activations").update(candidate_id,
                    expires_at=plan["renewed_expires_at"], lifecycle_renewal={
                        "operation_id": operation.operation_id, "actor_ref": operation.actor_ref,
                        "previous_expires_at": plan["previous_expires_at"], "expires_at": plan["renewed_expires_at"]})
                db.rollback()
            return {"ok": True, "status": "beta_renewed", "expires_at": plan["renewed_expires_at"]}
        if action == "promote_stable":
            from adaos.sdk.builder.applications import accept_local_trial
            from .trial_runtime import reviewed_beta_runtime
            with reviewed_beta_runtime(self.owner, candidate_id, candidate.release_digest), reviewed_publication(
                    {**operation.to_dict(), "action": action, "plan": plan}):
                if candidate.status == 'stale':
                    from adaos.services.root.service import RootDeveloperService
                    root = RootDeveloperService()
                    publication = root._artifact_publication_service(root._load_config(), migrate_legacy_layouts=False)
                    publication.prepare_candidate_public_source(candidate_id)
                    candidate = publication.candidate_store.load(candidate_id)
                self._verify_reviewed_beta(candidate, activation)
                result = accept_local_trial(application_id, webspace_id=plan["webspace_id"],
                    candidate_id=candidate_id, candidate_digest=candidate.package_digest, actor_ref=operation.actor_ref)
            selected = self.store.get_runtime_selection(plan["webspace_id"], application_id)
            if (not result.get("ok", True) or selected.source != "stable_installation"
                    or selected.runtime_root_ref != "workspace" or selected.release_digest != candidate.release_digest):
                raise RuntimeChannelConflict("StableAcceptanceNotConfirmed")
            return result
        if action == "retain_local_beta":
            if self.store.get_application(application_id).publisher_ref.lower() != operation.subnet_ref.lower():
                raise PermissionError("OnlyPublisherMayInstallLocalBeta")
            path = admission_path(self.state, candidate_id)
            if path.is_file():
                prior = json.loads(path.read_text(encoding="utf-8"))
                if prior.get("status") == "active":
                    from .local_beta_admission import read_local_beta_admission
                    read_local_beta_admission(self.state, candidate_id, candidate.release_digest, activation)
                    return {"ok": True, "status": "installed_local_beta", "receipt_digest": prior["receipt_digest"],
                            "observed_admission_operation_id": prior.get("operation_id"), "observation_only": True}
                if prior.get("status") != "revoked":
                    raise RuntimeChannelConflict("LocalBetaAdmissionAlreadyExists")
            NativeTrialRuntime.resolve(self.owner, candidate_id, candidate.release_digest)
            if candidate.status != "accepted" or (activation.get("health_evidence") or {}).get("status") != "passed":
                raise RuntimeChannelConflict("ExactHealthyAcceptedTrialRequired")
            inactive = plan.get("retention_mode") == "inactive_completed_trial"
            if inactive:
                evidence = self._inactive_beta_retention_evidence(
                    operation.application_id, candidate, activation
                )
                if evidence != plan.get("retention_evidence"):
                    raise RuntimeChannelConflict("ReviewedBetaRetentionEvidenceChanged")
            channel = ApplicationRuntimeChannel(self.state, application_id)
            expected = tuple(RuntimeSelection.from_mapping(s) for s in plan["expected_selections"])
            with channel._connection() as db:
                channel._initialize(db, expected)
                # Admission-only metadata neither changes executable code nor
                # data. Reserve the channel against other writers while
                # allowing the same immutable runtime's current readers/jobs.
                # Destructive transitions still require an exclusive fence.
                db.execute("BEGIN IMMEDIATE")
                channel._assert_available(db)
                actual = channel._decode(db.execute("SELECT document FROM channel WHERE id=1").fetchone()[0])
                selected_trial = bool(actual) and all(
                    s.runtime_root_ref == f"trial:{candidate_id}"
                    and s.release_digest == candidate.release_digest
                    for s in actual
                )
                inactive_stable = inactive and bool(actual) and all(
                    s.runtime_root_ref == "workspace"
                    and s.source == "stable_installation"
                    and s.release_digest == plan.get("stable_release_digest")
                    for s in actual
                )
                if not same_runtime_selections(actual, expected) or not (selected_trial or inactive_stable):
                    raise RuntimeChannelConflict("SelectedTrialChanged")
                receipt = {"schema": "adaos.application.local_beta_admission.v1", "status": "active",
                    "application_id": application_id, "candidate_id": candidate_id,
                    "publisher_ref": self.store.get_application(application_id).publisher_ref,
                    "candidate_digest": candidate.digest, "release_digest": candidate.release_digest,
                    "package_digest": candidate.package_digest, "runtime_binding_digest": plan["trial_binding_digest"],
                    "actor_ref": operation.actor_ref, "operation_id": operation.operation_id, "created_at": utc_now()}
                receipt["receipt_digest"] = canonical_payload_digest(receipt)
                atomic_write_json(path, receipt)
                # Only the receipt is written. Releasing this read/reservation
                # transaction by rollback needs no exclusive reader drain.
                db.rollback()
            return {"ok": True, "status": "installed_local_beta", "receipt_digest": receipt["receipt_digest"]}
        if action == "delete_trial":
            return self._delete_trial(operation, root, activation)
        if action == "recover_runtime":
            if plan.get("recovery_mode") == "cancel_finalize_before_effects":
                from .lifecycle_recovery import cancel_local_finalize_before_effects
                return cancel_local_finalize_before_effects(self.owner, candidate_id, release_digest=plan["release_digest"],
                    actor_ref=operation.actor_ref, subnet_ref=operation.subnet_ref, capability="applications.recover")
            from .lifecycle_recovery import abort_local_trial_preparation
            return abort_local_trial_preparation(self.owner, candidate_id, release_digest=plan["release_digest"],
                actor_ref=operation.actor_ref, subnet_ref=operation.subnet_ref, capability="applications.recover")
        return self._restore_source(operation)

    def _verify_reviewed_beta(self, candidate, activation):
        from .trial_runtime import reviewed_beta_runtime
        if candidate.status not in {"trial", "accepted"} or (activation.get("health_evidence") or {}).get("status") != "passed":
            raise RuntimeChannelConflict("ExactHealthyCandidateRequired")
        with reviewed_beta_runtime(self.owner, candidate.candidate_id, candidate.release_digest):
            runtime = NativeTrialRuntime.resolve(self.owner, candidate.candidate_id, candidate.release_digest)
            for package in runtime.packages:
                runtime.verified_source(package)

    def _restore_source(self, operation: ApplicationOperation) -> dict:
        from .local_release_transition import bind_local_data_lifecycle

        plan = operation.plan
        candidate_id, digest = plan["candidate_id"], plan["release_digest"]
        runtime = NativeTrialRuntime._resolve_immutable(self.owner, candidate_id, digest, for_data_recovery=True)
        release = self.store.get_release(operation.application_id, digest).project_release
        lifecycle = bind_local_data_lifecycle(self.owner, runtime, release, application_id=operation.application_id)
        metadata = self.workspace / ".adaos"

        def verify(_key):
            if plan["action"] == "uninstall_beta":
                return {"ok": True, "reason": "RetiredWithoutFallback", "data_policy": "retain"}
            if lifecycle.beta_source_ref and plan["action"] != "restore_stable":
                NativeTrialRuntime.resolve(self.owner, lifecycle.beta_source_ref[0].removeprefix("trial:"),
                                            lifecycle.beta_source_ref[1])
            elif lifecycle.stable_digest:
                lock = WorkspaceLock.from_mapping(json.loads((metadata / "workspace.lock.json").read_text(encoding="utf-8")))
                baseline = self.store.get_installation(operation.application_id)
                if (baseline.status != "active" or baseline.installed_release_digest != lifecycle.stable_digest
                        or not any(s.project_id == release.project_id and s.release_digest == lifecycle.stable_digest
                                   for s in lock.slots)):
                    raise RuntimeChannelConflict("StableSourceChanged")
            else:
                try:
                    installed = self.store.get_installation(operation.application_id)
                except FileNotFoundError:
                    installed = None
                if installed and installed.status != "removed":
                    raise RuntimeChannelConflict("InstallationAppeared")
            return {"ok": True, "reason": "VerifiedRetainedSource"}

        if plan["action"] == "recover_runtime":
            transition = lifecycle.abort_beta_preparation(verify_source=verify,
                source_guard=lambda: mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30))
        else:
            restored_source = None
            if plan["action"] == "restore_stable":
                restored_source = RuntimeSelection(webspace_id=plan["webspace_id"], application_id=operation.application_id,
                    source="stable_installation", release_digest=plan["stable_release_digest"],
                    runtime_root_ref="workspace", revision=1)
            transition = lifecycle.reject_beta(webspace_id=plan["webspace_id"], verify_source=verify,
                source_guard=lambda: mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30),
                verify_idle=lambda key: assert_idle_jobs(self.state, operation.application_id, runtime.root),
                restore_source=plan["action"] != "uninstall_beta",
                expected_current=tuple(RuntimeSelection.from_mapping(s) for s in plan["expected_selections"]),
                restored_source=restored_source)
        path = admission_path(self.state, candidate_id)
        if path.is_file():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            # A completed old transition can be acknowledged after another
            # reviewed admission. Never revoke that newer authority on replay.
            if receipt.get("receipt_digest") == plan.get("expected_admission_receipt_digest"):
                receipt.update(status="revoked", revoked_by_operation=operation.operation_id)
                receipt.pop("receipt_digest", None)
                receipt["receipt_digest"] = canonical_payload_digest(receipt)
                atomic_write_json(path, receipt)
        from .materialization import request_materialization
        refresh = request_materialization(self.owner, operation.application_id,
                                           webspace_ids=plan["affected_webspace_ids"])
        return {"ok": True, "status": "runtime_deactivated", "data_policy": "retain",
                "runtime_transition_operation_id": transition["operation_id"],
                "runtime_refresh": refresh}

    def _delete_trial(self, operation: ApplicationOperation, root: Path, activation: dict) -> dict:
        plan = operation.plan
        marker = activation.get("lifecycle_deletion") or {}
        if marker.get("operation_id") == operation.operation_id:
            return {"ok": True, "status": "trial_deleted_recoverably", "data_policy": "retain"}
        candidate_id = plan["candidate_id"]
        refs = self._references(operation.application_id, candidate_id, ignore_operation=operation.operation_id)
        if refs:
            raise RuntimeChannelConflict("TrialReferenced:" + ",".join(refs))
        assert_idle_jobs(self.state, operation.application_id, root)
        # Resolve the exact lexical target before any move; links/junctions
        # and caller-provided paths cannot broaden the deletion boundary.
        allowed = trial_workspace_root(self.workspace, candidate_id)
        if root != allowed or root.absolute() != root.resolve():
            raise RuntimeChannelConflict("ExactUnlinkedTrialRootRequired")
        archive = self.state / "artifact_pipeline/trial-deletions" / operation.operation_id / "runtime"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.resolve() != archive.absolute():
            raise RuntimeChannelConflict("TrialDeletionArchiveConflict")
        intent_path = archive.parent / "intent.json"
        intent = {"operation_id": operation.operation_id, "candidate_id": candidate_id,
                  "release_digest": plan["release_digest"], "binding_digest": plan["trial_binding_digest"],
                  "source": str(root), "archive": str(archive)}
        if intent_path.is_file():
            if json.loads(intent_path.read_text(encoding="utf-8")) != intent:
                raise RuntimeChannelConflict("TrialDeletionArchiveConflict")
        elif archive.exists():
            raise RuntimeChannelConflict("TrialDeletionArchiveHasNoExactIntent")
        else:
            atomic_write_json(intent_path, intent)
        with mutation_lock(self.state / "artifact_pipeline/trial-activations" / f"{candidate_id}.lifecycle.lock"):
            if self._references(operation.application_id, candidate_id, ignore_operation=operation.operation_id):
                raise RuntimeChannelConflict("TrialBecameReferenced")
            if root.is_dir() and not archive.exists():
                root.rename(archive)
            elif root.exists() or not archive.is_dir():
                raise RuntimeChannelConflict("ExactTrialDeletionMoveCannotBeReconciled")
            TrialActivationStore(self.state / "artifact_pipeline/trial-activations").update(candidate_id,
                status="detached", lifecycle_deletion={"operation_id": operation.operation_id,
                    "archive": str(archive), "data_policy": "retain", "deleted_at": utc_now()})
        return {"ok": True, "status": "trial_deleted_recoverably", "data_policy": "retain"}


def cancel_planned_operation(owner: Any, operation_id: str, *, plan_digest: str,
                             actor_ref: str, subnet_ref: str, capability: str) -> dict:
    operations = ApplicationLifecycleOperations(owner)
    operations._authorize(actor_ref, subnet_ref, capability, "applications.plan")
    op = operations.store.get_operation(operation_id)
    if op.plan_digest != plan_digest or (op.actor_ref, op.subnet_ref) != (actor_ref, subnet_ref):
        raise PermissionError("Exact original reviewed plan authority is required")
    if op.status == "cancelled":
        return op.to_dict()
    if op.status != "planned":
        raise RuntimeChannelConflict("OnlyUnstartedPlanCanBeCancelled")
    return operations.service._transition_operation(op, "cancelled").to_dict()
