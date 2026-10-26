"""Local Candidate decision authority with no Root/network/Builder dependency."""
from pathlib import Path
import json
from typing import Mapping, Any
from adaos.domain.application import utc_now as _now
from .candidates import CandidateRecord, CandidateStore, complete_trial
from .storage import atomic_write_json, mutation_lock, replace_with_retry
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
        # The terminal Candidate and the activation are separate files. Keep
        # the exact intent before moving a tree, and reconcile an interrupted
        # acknowledgement instead of making another authoring decision.
        import hashlib
        token = hashlib.sha256(candidate_id.encode()).hexdigest()
        with mutation_lock(self.state_root / "candidate-decisions" / f"{token}.lock"):
            return self._decide_locked(candidate_id, accepted=accepted, observations=observations)

    def _decide_locked(self, candidate_id, *, accepted, observations):
        candidate = self.candidate_store.load(candidate_id)
        wanted = "accepted" if accepted else "rejected"
        if candidate.status == wanted:
            return self._reconcile_terminal(candidate, accepted=accepted)
        running = next((item for item in candidate.trials if item.status == "running"), None)
        if running is None:
            raise LocalCandidateDecisionError("candidate has no running trial")
        rollback_receipt: dict[str, Any]
        trial_workspace: Path | None = None
        archive: Path | None = None
        activation = self.trial_activations.load(candidate_id)
        activation_status_before = str((activation or {}).get("status") or "").strip()
        activation_quiesced = False
        decision_committed = False
        intent_path = self.state_root / "candidate-decisions" / candidate_id / "intent.json"
        intent = {"candidate_id": candidate_id, "candidate_digest": candidate.digest,
                  "trial_id": running.trial_id, "accepted": accepted}
        if intent_path.is_file():
            if json.loads(intent_path.read_text(encoding="utf-8")) != intent:
                raise LocalCandidateDecisionError("candidate has another retained decision intent")
        else:
            atomic_write_json(intent_path, intent)
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
                if not trial_workspace.is_dir() and not archive.is_dir():
                    raise LocalCandidateDecisionError(
                        "rejected trial Workspace is missing before rollback"
                    )
                if archive.exists() and trial_workspace.exists():
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
                if trial_workspace.exists():
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
            decision_committed = True
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
            # save() can commit and lose its acknowledgement. Once the exact
            # terminal decision is durable, never restore executable rejected
            # code or rewind its activation. Recovery finishes metadata only.
            persisted = self.candidate_store.load(candidate_id)
            decision_committed = decision_committed or (persisted.status == wanted and persisted.digest == candidate.digest)
            if (
                not decision_committed and not accepted
                and archive is not None
                and archive.exists()
                and trial_workspace is not None
                and not trial_workspace.exists()
            ):
                self._switch_workspace(archive, trial_workspace)
            if activation_quiesced and not decision_committed:
                self.trial_activations.update(
                    candidate_id,
                    status=activation_status_before,
                    detaching_at=None,
                )
            raise
        return candidate

    def _reconcile_terminal(self, candidate, *, accepted):
        completed = next((t for t in reversed(candidate.trials) if t.status == ("accepted" if accepted else "rejected")), None)
        if completed is None:
            raise LocalCandidateDecisionError("terminal Candidate lacks exact completed Trial evidence")
        receipt = dict(completed.rollback_receipt or {})
        if not accepted:
            archive = self.state_root / "trial-rollbacks" / candidate.candidate_id / completed.trial_id / "workspace"
            if (receipt.get("status") != "rolled_back" or receipt.get("archive") != str(archive)
                    or not archive.is_dir() or self._trial_workspace(candidate.candidate_id).exists()):
                raise LocalCandidateDecisionError("terminal rejection requires its exact retained archive")
        activation = self.trial_activations.load(candidate.candidate_id)
        if activation:
            expected = "completed" if accepted else "detached"
            if activation.get("status") != expected:
                self.trial_activations.update(candidate.candidate_id, status=expected, rollback=receipt,
                    completed_at=_now() if accepted else None, detached_at=None if accepted else _now())
        return candidate
