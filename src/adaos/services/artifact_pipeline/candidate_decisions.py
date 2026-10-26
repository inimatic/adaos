"""Local Candidate decision authority with no Root/network/Builder dependency."""
from pathlib import Path
from typing import Mapping, Any
from adaos.domain.application import utc_now as _now
from .candidates import CandidateRecord, CandidateStore, complete_trial
from .storage import replace_with_retry
from .trial_activation import TrialActivationStore, trial_workspace_root


class LocalCandidateDecisionError(RuntimeError):
    pass


class LocalCandidateDecisionService:
    def __init__(self, *, state_root: Path, workspace_root: Path, trial_workspace=None,
                 candidate_store=None, trial_activations=None, workspace_switch=None):
        self.state_root = Path(state_root)
        self.workspace_root = Path(workspace_root)
        self.candidate_store = candidate_store or CandidateStore(self.state_root / "candidates")
        self.trial_activations = trial_activations or TrialActivationStore(self.state_root / "trial-activations")
        self._trial_workspace = trial_workspace or (lambda candidate_id: trial_workspace_root(self.workspace_root, candidate_id))
        self._switch_workspace = workspace_switch or replace_with_retry

    def decide_candidate(
        self,
        candidate_id: str,
        *,
        accepted: bool,
        observations: tuple[Mapping[str, Any], ...] = (),
    ) -> CandidateRecord:
        candidate = self.candidate_store.load(candidate_id)
        running = next((item for item in candidate.trials if item.status == "running"), None)
        if running is None:
            raise LocalCandidateDecisionError("candidate has no running trial")
        rollback_receipt: dict[str, Any]
        trial_workspace: Path | None = None
        archive: Path | None = None
        activation = self.trial_activations.load(candidate_id)
        activation_status_before = str((activation or {}).get("status") or "").strip()
        activation_quiesced = False
        try:
            if accepted:
                rollback_receipt = {
                    "status": "not_required",
                    "reason": "trial accepted for promotion",
                    "recorded_at": _now(),
                }
            else:
                trial_workspace = self._trial_workspace(candidate_id)
                archive = (
                    self.state_root
                    / "trial-rollbacks"
                    / candidate_id
                    / running.trial_id
                    / "workspace"
                )
                if not trial_workspace.is_dir():
                    raise LocalCandidateDecisionError(
                        "rejected trial Workspace is missing before rollback"
                    )
                if archive.exists():
                    raise LocalCandidateDecisionError(
                        "rejected trial rollback archive already exists"
                    )
                archive.parent.mkdir(parents=True, exist_ok=True)
                if activation is not None and activation_status_before in {
                    "active",
                    "completed",
                }:
                    self.trial_activations.update(
                        candidate_id,
                        status="detaching",
                        detaching_at=_now(),
                    )
                    activation_quiesced = True
                # Once the activation is no longer selectable, allow in-flight
                # Windows skill reads to release their handles before moving the
                # immutable Trial tree.  This retries only the filesystem switch;
                # it never repeats the candidate decision.
                self._switch_workspace(trial_workspace, archive, attempts=64)
                rollback_receipt = {
                    "status": "rolled_back",
                    "mode": "isolated_workspace_detached",
                    "archive": str(archive),
                    "recorded_at": _now(),
                }
            candidate = complete_trial(
                candidate,
                trial_id=running.trial_id,
                accepted=accepted,
                now=_now(),
                observations=observations,
                rollback_receipt=rollback_receipt,
            )
            self.candidate_store.save(candidate)
            if activation is not None:
                now = _now()
                self.trial_activations.update(
                    candidate_id,
                    status="completed" if accepted else "detached",
                    completed_at=now if accepted else None,
                    detached_at=now if not accepted else None,
                    rollback=rollback_receipt,
                )
        except Exception:
            if (
                not accepted
                and archive is not None
                and archive.exists()
                and trial_workspace is not None
                and not trial_workspace.exists()
            ):
                self._switch_workspace(archive, trial_workspace)
            if activation_quiesced:
                self.trial_activations.update(
                    candidate_id,
                    status=activation_status_before,
                    detaching_at=None,
                )
            raise
        return candidate
