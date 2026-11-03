"""Bind a fresh Stable cutover to a reviewed Core intent after verified recovery."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .runtime_channel import RuntimeChannelConflict
from .runtime_transition import ApplicationRuntimeTransition


_REVIEW: ContextVar[dict | None] = ContextVar("application_publication_review", default=None)
_SCHEMA = "adaos.application.stable_adoption_attempt.v1"


class PublicationReviewRequired(RuntimeChannelConflict):
    def result(self):
        return {"ok": False, "status": "blocked", "error": "cancelled_publication_requires_new_review",
            "message": str(self)}


def observed_recovered_adoption(state_dir: Path, application_id: str, candidate_id: str) -> str | None:
    from .runtime_channel import ApplicationRuntimeChannel
    runner = ApplicationRuntimeTransition(ApplicationRuntimeChannel(state_dir, application_id))
    record = runner.current(f"application-stable:{application_id}:{candidate_id}")
    return canonical_payload_digest(record) if record and record.get("aborted") else None


@contextmanager
def reviewed_publication(operation: Mapping[str, Any]):
    """Called only after Core admits the stored publisher/actor/intent."""
    intent = operation.get("intent") or operation.get("plan") or {}
    review = None
    if operation.get("action") == "promote_stable" and intent.get("candidate_id"):
        review = {"operation_id": operation["operation_id"], "application_id": operation["application_id"],
            "candidate_id": intent["candidate_id"], "actor_ref": operation["actor_ref"],
            "recovery_digest": operation.get("recovered_adoption_digest", intent.get("recovered_adoption_digest"))}
    token = _REVIEW.set(review)
    try:
        yield
    finally:
        _REVIEW.reset(token)


class StableAdoptionAttempts:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.root = lifecycle.recovery / "stable-attempts"
        self.base = f"application-stable:{lifecycle.application_id}:{lifecycle.candidate_id}"
        self.runner = ApplicationRuntimeTransition(lifecycle.channel)

    def _identity(self):
        value = self.lifecycle
        return {"schema": _SCHEMA, "application_id": value.application_id,
            "candidate_id": value.candidate_id, "release_digest": value.release_digest,
            "contract_digest": value._contract(), "base_operation_id": self.base}

    def _recovered(self, record):
        recovery = (record or {}).get("recovery") or {}
        steps = (recovery.get("intent") or {}).get("steps") or []
        receipts = recovery.get("receipts") or {}
        intent = (record or {}).get("intent") or {}
        target = intent.get("target") or {}
        expected = intent.get("expected") or []
        if (not record or not record.get("aborted") or record.get("completed")
                or recovery.get("running_step") or not steps
                or any((receipts.get(step) or {}).get("ok") is not True for step in steps)
                or intent.get("contract_digest") != self.lifecycle._contract()
                or target.get("release_digest") != self.lifecycle.release_digest
                or target.get("runtime_root_ref") != "workspace" or not expected
                or any(item.get("runtime_root_ref") != f"trial:{self.lifecycle.candidate_id}"
                       or item.get("release_digest") != self.lifecycle.release_digest for item in expected)):
            raise RuntimeChannelConflict("Finish verification of the previous Stable recovery before a new Finalize")

    def _read(self, path: Path):
        if not path.is_file():
            return None
        binding = json.loads(path.read_text(encoding="utf-8"))
        digest = binding.get("binding_digest")
        unsigned = {key: value for key, value in binding.items() if key != "binding_digest"}
        area = binding.get("area")
        expected_operation = self.base
        if area is not None:
            if area != hashlib.sha256(str(binding.get("review_id") or "").encode()).hexdigest():
                raise RuntimeChannelConflict("Stable attempt identity changed")
            expected_operation += ":attempt:" + area
        if (digest != canonical_payload_digest(unsigned)
                or any(binding.get(key) != value for key, value in self._identity().items())
                or binding.get("operation_id") != expected_operation):
            raise RuntimeChannelConflict("Retained Stable attempt binding changed")
        if binding.get("previous_operation_id"):
            previous = self.runner.get(binding["previous_operation_id"])
            self._recovered(previous)
            if canonical_payload_digest(previous) != binding.get("recovery_digest"):
                raise RuntimeChannelConflict("Previous Stable recovery evidence changed")
        return binding

    def current(self):
        return self._read(self.root / "current.json")

    def select(self):
        review = _REVIEW.get()
        with mutation_lock(self.root / ".selection.lock", timeout_s=30):
            current = self.current()
            if review is None:
                if current and current.get("area"):
                    raise RuntimeChannelConflict("Recover this Stable attempt through its original Core operation")
                return current
            if any(review[key] != getattr(self.lifecycle, key) for key in ("application_id", "candidate_id")):
                raise RuntimeChannelConflict("Publication review belongs to another Candidate")
            review_path = self.root / "reviews" / (hashlib.sha256(review["operation_id"].encode()).hexdigest() + ".json")
            retained = self._read(review_path)
            if retained:
                if retained["review_id"] != review["operation_id"]:
                    raise RuntimeChannelConflict("Publication review identity changed")
                return retained
            operation_id = current["operation_id"] if current else self.base
            previous = self.runner.get(operation_id)
            if previous and (previous.get("aborted") or previous.get("recovery")):
                self._recovered(previous)
                if review["recovery_digest"] != canonical_payload_digest(previous):
                    raise PublicationReviewRequired("The cancelled publication requires a new reviewed Finalize")
                area = hashlib.sha256(review["operation_id"].encode()).hexdigest()
                binding = {**self._identity(), "operation_id": self.base + ":attempt:" + area,
                    "area": area, "previous_operation_id": operation_id,
                    "recovery_digest": canonical_payload_digest(previous)}
            elif current:
                # Continue the same unfinished cutover; never fork an unknown effect.
                if current["review_id"] != review["operation_id"]:
                    if previous and previous.get("completed"):
                        return current
                    raise RuntimeChannelConflict("Another reviewed publication owns the current Stable attempt")
                binding = dict(current)
            else:
                binding = {**self._identity(), "operation_id": self.base, "area": None}
            if binding.get("review_id") != review["operation_id"]:
                binding.update(review_id=review["operation_id"], actor_ref=review["actor_ref"], created_at=utc_now())
            binding.pop("binding_digest", None)
            binding["binding_digest"] = canonical_payload_digest(binding)
            atomic_write_json(self.root / "current.json", binding)
            atomic_write_json(review_path, binding)
            return binding
