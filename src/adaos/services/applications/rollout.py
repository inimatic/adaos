from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .service import ApplicationService, ApplicationServiceError


class ApplicationRolloutError(ApplicationServiceError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationRolloutError("Application rollout state is unreadable") from exc
    if not isinstance(value, dict):
        raise ApplicationRolloutError("Application rollout state is invalid")
    return value


class ApplicationRolloutService:
    """One sticky rollout policy over the canonical prerelease pointer."""

    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications
        self.store = applications.store

    @property
    def root(self) -> Path:
        path = self.store.root / "rollouts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _policy_path(self, application_id: str) -> Path:
        return self.root / "policies" / f"{_digest_key(application_id)}.json"

    def _event_path(self, event_id: str) -> Path:
        return self.root / "health" / f"{_digest_key(event_id)}.json"

    def get_policy(self, application_id: str) -> dict[str, Any] | None:
        path = self._policy_path(application_id)
        if not path.is_file():
            return None
        value = _read(path)
        if (
            value.get("schema") != "adaos.application.prerelease_rollout.v1"
            or value.get("application_id") != application_id
        ):
            raise ApplicationRolloutError("Application rollout policy identity is invalid")
        return value

    def set_policy(
        self,
        application_id: str,
        *,
        release_digest: str,
        publisher_ref: str,
        percentage: int,
        paused: bool,
        minimum_health_subnets: int,
        failure_threshold: float,
        expected_revision: int,
        idempotency_key: str,
        resume_after_halt: bool = False,
    ) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise ApplicationRolloutError("only the Application publisher may change rollout")
        channels = self.store.get_channels(application_id).get("channels") or {}
        if channels.get("prerelease") != release_digest:
            raise ApplicationRolloutError("rollout must name the exact current prerelease")
        if not channels.get("stable"):
            raise ApplicationRolloutError("staged rollout requires an existing stable release")
        if isinstance(percentage, bool) or not 0 <= int(percentage) <= 100:
            raise ApplicationRolloutError("rollout percentage must be between 0 and 100")
        if isinstance(minimum_health_subnets, bool) or not 1 <= int(minimum_health_subnets) <= 10_000:
            raise ApplicationRolloutError("minimum_health_subnets must be between 1 and 10000")
        threshold = float(failure_threshold)
        if not 0.0 < threshold <= 1.0:
            raise ApplicationRolloutError("failure_threshold must be greater than zero and at most one")
        key = str(idempotency_key or "").strip()
        if not key:
            raise ApplicationRolloutError("idempotency_key is required")
        intent = {
            "application_id": application_id,
            "release_digest": release_digest,
            "publisher_ref": publisher_ref,
            "percentage": int(percentage),
            "paused": bool(paused),
            "minimum_health_subnets": int(minimum_health_subnets),
            "failure_threshold": threshold,
            "resume_after_halt": bool(resume_after_halt),
            "expected_revision": int(expected_revision),
        }
        intent_digest = canonical_payload_digest(intent)
        index_path = self.root / "idempotency" / f"{_digest_key(key)}.json"
        path = self._policy_path(application_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if index_path.is_file():
                indexed = _read(index_path)
                if indexed.get("intent_digest") != intent_digest:
                    raise ApplicationRolloutError("rollout idempotency key names another intent")
                response = indexed.get("response")
                if not isinstance(response, dict):
                    raise ApplicationRolloutError("rollout idempotency receipt is stale")
                return response
            current = self.get_policy(application_id)
            observed = int(current.get("revision") or 0) if current else 0
            if observed != expected_revision:
                raise ApplicationRolloutError(
                    f"rollout revision conflict: expected {expected_revision}, observed {observed}"
                )
            if current and current.get("status") == "halted" and not resume_after_halt:
                raise ApplicationRolloutError("halted rollout requires explicit resume_after_halt")
            now = _now()
            value = {
                "schema": "adaos.application.prerelease_rollout.v1",
                **intent,
                "salt": (
                    str(current.get("salt"))
                    if current and current.get("release_digest") == release_digest
                    else _digest_key(f"{application_id}:{release_digest}:{key}")
                ),
                "status": "paused" if paused else "active",
                "halted_reason": None,
                "revision": observed + 1,
                "created_at": str(current.get("created_at")) if current else now,
                "updated_at": now,
            }
            value.pop("resume_after_halt", None)
            value.pop("expected_revision", None)
            atomic_write_json(path, value)
            atomic_write_json(
                index_path,
                {
                    "schema": "adaos.application.rollout_idempotency.v1",
                    "idempotency_key_hash": f"sha256:{_digest_key(key)}",
                    "intent_digest": intent_digest,
                    "application_id": application_id,
                    "revision": value["revision"],
                    "response": value,
                },
            )
            return value

    def assignment(
        self,
        application_id: str,
        release_digest: str,
        *,
        subscriber_subnet_ref: str | None,
    ) -> dict[str, Any]:
        policy = self.get_policy(application_id)
        if policy is None or policy.get("release_digest") != release_digest:
            return {
                "schema": "adaos.application.prerelease_assignment.v1",
                "application_id": application_id,
                "release_digest": release_digest,
                "subscriber_subnet_ref": subscriber_subnet_ref,
                "eligible": True,
                "reason": "unrestricted",
                "bucket": None,
                "percentage": 100,
                "policy_revision": None,
            }
        subscriber = str(subscriber_subnet_ref or "").strip().lower()
        if not subscriber:
            eligible = int(policy["percentage"]) == 100 and policy["status"] == "active"
            bucket = None
            reason = "eligible" if eligible else "subscriber_identity_required"
        elif subscriber == policy["publisher_ref"]:
            eligible = policy["status"] == "active"
            bucket = 0
            reason = "publisher" if eligible else str(policy["status"])
        else:
            bucket = int(
                hashlib.sha256(
                    f"{policy['salt']}:{application_id}:{release_digest}:{subscriber}".encode("utf-8")
                ).hexdigest()[:16],
                16,
            ) % 10_000
            eligible = policy["status"] == "active" and bucket < int(policy["percentage"]) * 100
            reason = "eligible" if eligible else str(policy["status"] if policy["status"] != "active" else "outside_stage")
        return {
            "schema": "adaos.application.prerelease_assignment.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "subscriber_subnet_ref": subscriber or None,
            "eligible": eligible,
            "reason": reason,
            "bucket": bucket,
            "percentage": int(policy["percentage"]),
            "policy_revision": int(policy["revision"]),
        }

    def list_health(self, application_id: str, release_digest: str) -> list[dict[str, Any]]:
        parent = self.root / "health"
        values = [_read(path) for path in parent.glob("*.json")] if parent.is_dir() else []
        return sorted(
            (
                item
                for item in values
                if item.get("application_id") == application_id
                and item.get("release_digest") == release_digest
            ),
            key=lambda item: (str(item.get("observed_at")), str(item.get("event_id"))),
        )

    def health_summary(self, application_id: str, release_digest: str) -> dict[str, Any]:
        latest: dict[str, dict[str, Any]] = {}
        for event in self.list_health(application_id, release_digest):
            latest[str(event["subscriber_subnet_ref"])] = event
        failures = sum(1 for item in latest.values() if item["outcome"] == "failed")
        total = len(latest)
        return {
            "schema": "adaos.application.prerelease_health_summary.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "distinct_subnets": total,
            "healthy_subnets": total - failures,
            "failed_subnets": failures,
            "failure_rate": failures / total if total else 0.0,
        }

    def record_health(
        self,
        application_id: str,
        release_digest: str,
        *,
        subscriber_subnet_ref: str,
        outcome: str,
        installation_revision: int,
        evidence_digest: str,
        observed_at: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        policy = self.get_policy(application_id)
        if policy is None or policy.get("release_digest") != release_digest:
            raise ApplicationRolloutError("health report does not name an active rollout")
        subscriber = str(subscriber_subnet_ref or "").strip().lower()
        if not subscriber.startswith("subnet:"):
            raise ApplicationRolloutError("subscriber_subnet_ref must use subnet:<id>")
        if outcome not in {"healthy", "failed"}:
            raise ApplicationRolloutError("rollout health outcome must be healthy or failed")
        if isinstance(installation_revision, bool) or int(installation_revision) < 1:
            raise ApplicationRolloutError("installation_revision must be positive")
        if not str(evidence_digest).startswith("sha256:") or len(str(evidence_digest)) != 71:
            raise ApplicationRolloutError("evidence_digest must be sha256")
        try:
            observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApplicationRolloutError("observed_at must be ISO-8601") from exc
        if observed.tzinfo is None:
            raise ApplicationRolloutError("observed_at must include a timezone")
        normalized_observed_at = observed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        key = str(idempotency_key or "").strip()
        if not key:
            raise ApplicationRolloutError("idempotency_key is required")
        event_id = f"rollouthealth.{_digest_key(f'{application_id}:{release_digest}:{key}')[:32]}"
        event = {
            "schema": "adaos.application.prerelease_health.v1",
            "event_id": event_id,
            "application_id": application_id,
            "release_digest": release_digest,
            "subscriber_subnet_ref": subscriber,
            "outcome": outcome,
            "installation_revision": int(installation_revision),
            "evidence_digest": str(evidence_digest),
            "observed_at": normalized_observed_at,
            "received_at": _now(),
        }
        path = self._event_path(event_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                existing = _read(path)
                comparable = {key: value for key, value in event.items() if key != "received_at"}
                observed = {key: value for key, value in existing.items() if key != "received_at"}
                if observed != comparable:
                    raise ApplicationRolloutError("rollout health idempotency conflict")
                current = self.get_policy(application_id)
                return {
                    "event": existing,
                    "summary": self.health_summary(application_id, release_digest),
                    "halted": bool(current and current.get("status") == "halted"),
                    "policy": current,
                }
            assignment = self.assignment(
                application_id,
                release_digest,
                subscriber_subnet_ref=subscriber,
            )
            if not assignment["eligible"]:
                raise ApplicationRolloutError(
                    "subscriber is not assigned to this prerelease rollout"
                )
            atomic_write_json(path, event)
            summary = self.health_summary(application_id, release_digest)
            current = self.get_policy(application_id)
            assert current is not None
            should_halt = (
                current["status"] == "active"
                and summary["distinct_subnets"] >= int(current["minimum_health_subnets"])
                and summary["failure_rate"] >= float(current["failure_threshold"])
            )
            if should_halt:
                current = {
                    **current,
                    "status": "halted",
                    "paused": True,
                    "halted_reason": "failure_threshold_reached",
                    "revision": int(current["revision"]) + 1,
                    "updated_at": _now(),
                }
                atomic_write_json(self._policy_path(application_id), current)
            return {"event": event, "summary": summary, "halted": should_halt, "policy": current}


__all__ = ["ApplicationRolloutError", "ApplicationRolloutService"]
