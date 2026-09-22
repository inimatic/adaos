"""Derived evidence freshness and bounded external-dependency monitoring.

Evidence claims remain immutable historical facts.  This module compares a
claim with current observations and policy, producing a disposable assessment
that can be rebuilt without rewriting either packages or claims.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    EvidenceAssessment,
    EvidenceClaim,
    ExternalDependencyObservation,
)


_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")


class EvidenceFreshnessError(ValueError):
    """The assessment context or external observation is invalid."""


def _utc(value: str, *, field: str) -> datetime:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceFreshnessError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceFreshnessError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise EvidenceFreshnessError("assessment time must include a timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _policy(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    purpose = str(result.get("purpose") or "production").strip().lower()
    if purpose not in {"semantic", "simulation", "production"}:
        raise EvidenceFreshnessError("evidence policy purpose must be semantic, simulation, or production")
    result["purpose"] = purpose
    result["policy_ref"] = str(result.get("policy_ref") or f"evidence-policy:{purpose}").strip()
    for field in ("max_claim_age_seconds", "max_observation_age_seconds"):
        if result.get(field) is None:
            continue
        result[field] = int(result[field])
        if result[field] < 0:
            raise EvidenceFreshnessError(f"{field} cannot be negative")
    result["allow_stale"] = bool(result.get("allow_stale", purpose != "production"))
    result["trusted_issuers"] = sorted(
        {str(item).strip() for item in result.get("trusted_issuers") or [] if str(item).strip()}
    )
    result["trusted_dependency_domains"] = sorted(
        {
            str(item).strip()
            for item in result.get("trusted_dependency_domains") or []
            if str(item).strip()
        }
    )
    return result


@dataclass(frozen=True, slots=True)
class EvidenceAssessmentResult:
    assessment: EvidenceAssessment
    explanation: Mapping[str, Any]

    @property
    def status(self) -> str:
        return str(self.assessment.to_dict()["status"])

    @property
    def admissible_for_use(self) -> bool:
        return bool(self.explanation["admissible_for_use"])


class EvidenceAssessmentService:
    """Rebuild current trust from immutable claims and observations."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))

    def assess(
        self,
        claim: EvidenceClaim,
        *,
        policy: Mapping[str, Any],
        environment_profile_digest: str,
        subject_digests: Mapping[str, str],
        dependency_observations: Mapping[str, ExternalDependencyObservation],
        revoked_claim_digests: Iterable[str] = (),
        superseding_claims: Iterable[EvidenceClaim] = (),
        source_policy_digest: str | None = None,
    ) -> EvidenceAssessmentResult:
        value = claim.to_dict()
        normalized_policy = _policy(policy)
        policy_digest = canonical_payload_digest(normalized_policy)
        evaluated = self._now().astimezone(timezone.utc)
        reasons: list[str] = []
        status = "admissible"

        revoked = {str(item) for item in revoked_claim_digests}
        if claim.digest in revoked:
            status = "revoked"
            reasons.append("claim explicitly revoked")
        elif value["result"] == "incompatible":
            status = "incompatible"
            reasons.append("verification result is incompatible")
        else:
            issuer = str(value["provenance"]["issuer"])
            trusted_issuers = set(normalized_policy["trusted_issuers"])
            if trusted_issuers and issuer not in trusted_issuers:
                status = "incompatible"
                reasons.append(f"issuer is outside the trusted set: {issuer}")

        if status == "admissible" and self._is_superseded(claim, tuple(superseding_claims)):
            status = "superseded"
            reasons.append("a newer claim covers the same subjects and environment")

        stale_reasons: list[str] = []
        invalidated_by = set(value["freshness"]["invalidated_by"])
        if status == "admissible":
            issued = _utc(value["issued_at"], field="claim.issued_at")
            claim_age = max(0.0, (evaluated - issued).total_seconds())
            declared_age = int(value["freshness"]["max_age_seconds"])
            policy_age = normalized_policy.get("max_claim_age_seconds")
            maximum_age = min(declared_age, int(policy_age)) if policy_age is not None else declared_age
            if claim_age > maximum_age:
                stale_reasons.append(
                    f"claim age {int(claim_age)}s exceeds maximum {maximum_age}s"
                )

            current_environment = str(environment_profile_digest or "").strip()
            if value["environment"]["profile_digest"] != current_environment:
                if "environment_change" in invalidated_by:
                    stale_reasons.append("environment profile digest changed")
                else:
                    status = "incompatible"
                    reasons.append("claim environment does not match the requested environment")

            changed_subjects = [
                str(item["ref"])
                for item in value["subjects"]
                if subject_digests.get(str(item["ref"])) not in {None, str(item["digest"])}
            ]
            if changed_subjects:
                status = "superseded"
                reasons.append("subject digest changed: " + ", ".join(sorted(changed_subjects)))

            if (
                status == "admissible"
                and source_policy_digest
                and source_policy_digest != policy_digest
                and "policy_change" in invalidated_by
            ):
                stale_reasons.append("assessment policy changed")

            if status == "admissible":
                stale_reasons.extend(
                    self._dependency_reasons(
                        value["dependencies"],
                        observations=dependency_observations,
                        evaluated_at=evaluated,
                        policy=normalized_policy,
                        invalidated_by=invalidated_by,
                    )
                )

        if status == "admissible" and stale_reasons:
            status = "stale"
            reasons.extend(stale_reasons)

        admissible_for_use = status == "admissible" or (
            status == "stale" and bool(normalized_policy["allow_stale"])
        )
        short = claim.digest.removeprefix("sha256:")[:16]
        policy_short = policy_digest.removeprefix("sha256:")[:16]
        assessment = EvidenceAssessment.create(
            assessment_ref=f"evidence-assessment:{short}/{policy_short}",
            claim_ref=claim.claim_ref,
            claim_digest=claim.digest,
            evaluated_at=_iso(evaluated),
            policy_digest=policy_digest,
            status=status,
            reasons=tuple(dict.fromkeys(reasons)),
        )
        explanation = {
            "schema": "adaos.evidence.explanation.v1",
            "claim_ref": claim.claim_ref,
            "claim_digest": claim.digest,
            "status": status,
            "purpose": normalized_policy["purpose"],
            "admissible_for_use": admissible_for_use,
            "historically_verified": value["result"] == "verified",
            "requires_reverification": status == "stale",
            "message": self._message(status, reasons, purpose=normalized_policy["purpose"]),
            "reasons": list(dict.fromkeys(reasons)),
            "policy_digest": policy_digest,
        }
        return EvidenceAssessmentResult(assessment=assessment, explanation=explanation)

    @staticmethod
    def _is_superseded(claim: EvidenceClaim, candidates: Sequence[EvidenceClaim]) -> bool:
        value = claim.to_dict()
        subject_refs = {str(item["ref"]) for item in value["subjects"]}
        issued = _utc(value["issued_at"], field="claim.issued_at")
        for candidate in candidates:
            if candidate.digest == claim.digest:
                continue
            other = candidate.to_dict()
            if other["claim_kind"] != value["claim_kind"]:
                continue
            if other["environment"] != value["environment"]:
                continue
            if {str(item["ref"]) for item in other["subjects"]} != subject_refs:
                continue
            if _utc(other["issued_at"], field="claim.issued_at") > issued:
                return True
        return False

    @staticmethod
    def _dependency_reasons(
        dependencies: Sequence[Mapping[str, Any]],
        *,
        observations: Mapping[str, ExternalDependencyObservation],
        evaluated_at: datetime,
        policy: Mapping[str, Any],
        invalidated_by: set[str],
    ) -> list[str]:
        if "dependency_change" not in invalidated_by:
            return []
        reasons: list[str] = []
        trusted_domains = set(policy["trusted_dependency_domains"])
        maximum_age = policy.get("max_observation_age_seconds")
        for dependency in dependencies:
            dependency_ref = str(dependency["ref"])
            observation = observations.get(dependency_ref)
            if observation is None:
                reasons.append(f"current dependency observation is missing: {dependency_ref}")
                continue
            current = observation.to_dict()
            if trusted_domains and current["trust_domain"] not in trusted_domains:
                reasons.append(
                    f"dependency observation has untrusted domain: {dependency_ref}"
                )
                continue
            if maximum_age is not None:
                observed_at = _utc(current["observed_at"], field="observation.observed_at")
                age = max(0.0, (evaluated_at - observed_at).total_seconds())
                if age > int(maximum_age):
                    reasons.append(
                        f"dependency observation is stale: {dependency_ref} ({int(age)}s)"
                    )
                    continue
            expected_version = dependency.get("observed_version")
            if expected_version is not None and current.get("observed_version") != expected_version:
                reasons.append(f"dependency version changed: {dependency_ref}")
            expected_fingerprint = dependency.get("fingerprint")
            if expected_fingerprint is not None and current.get("fingerprint") != expected_fingerprint:
                reasons.append(f"dependency fingerprint changed: {dependency_ref}")
        return reasons

    @staticmethod
    def _message(status: str, reasons: Sequence[str], *, purpose: str) -> str:
        detail = "; ".join(reasons)
        if status == "admissible":
            return f"Evidence is current and admissible for {purpose}."
        if status == "stale":
            return (
                "Evidence was historically verified but is stale"
                + (f": {detail}." if detail else ".")
            )
        if status == "incompatible":
            return "Current verification is incompatible" + (f": {detail}." if detail else ".")
        if status == "superseded":
            return "Evidence is historical and superseded" + (f": {detail}." if detail else ".")
        return "Evidence has been revoked" + (f": {detail}." if detail else ".")


class ExternalChangeMonitorStore:
    """Content-addressed store for immutable external observations."""

    def __init__(
        self,
        root: Path,
        *,
        event_sink: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.observations_dir = self.root / "observations"
        self.event_sink = event_sink

    def observe(
        self,
        *,
        dependency_ref: str,
        observed_at: str,
        trust_domain: str,
        source: Mapping[str, Any],
        provenance: Mapping[str, Any],
        observed_version: str | None = None,
        fingerprint: str | None = None,
    ) -> ExternalDependencyObservation:
        seed = {
            "dependency_ref": str(dependency_ref).strip(),
            "observed_at": observed_at,
            "trust_domain": str(trust_domain).strip(),
            "source": dict(source),
            "provenance": dict(provenance),
            "observed_version": observed_version,
            "fingerprint": fingerprint,
        }
        token = canonical_payload_digest(seed).removeprefix("sha256:")[:24]
        observation = ExternalDependencyObservation.create(
            observation_ref=f"external-observation:{token}",
            dependency_ref=dependency_ref,
            observed_at=observed_at,
            trust_domain=trust_domain,
            source=source,
            provenance=provenance,
            observed_version=observed_version,
            fingerprint=fingerprint,
        )
        self._write_once(observation)
        if self.event_sink is not None:
            self.event_sink(
                {
                    "type": "cbs.external_dependency.observed",
                    "dependency_ref": observation.dependency_ref,
                    "observation_digest": observation.digest,
                    "observed_at": observation.to_dict()["observed_at"],
                }
            )
        return observation

    def history(self, dependency_ref: str) -> tuple[ExternalDependencyObservation, ...]:
        if not self.observations_dir.exists():
            return ()
        result: list[ExternalDependencyObservation] = []
        for path in self.observations_dir.glob("*.json"):
            try:
                item = ExternalDependencyObservation.from_mapping(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if item.dependency_ref == dependency_ref:
                result.append(item)
        return tuple(
            sorted(
                result,
                key=lambda item: (item.to_dict()["observed_at"], item.digest),
            )
        )

    def latest(self, dependency_ref: str) -> ExternalDependencyObservation | None:
        items = self.history(dependency_ref)
        return items[-1] if items else None

    def latest_map(self) -> dict[str, ExternalDependencyObservation]:
        latest: dict[str, ExternalDependencyObservation] = {}
        if not self.observations_dir.exists():
            return latest
        for path in self.observations_dir.glob("*.json"):
            try:
                item = ExternalDependencyObservation.from_mapping(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            current = latest.get(item.dependency_ref)
            if current is None or (
                item.to_dict()["observed_at"], item.digest
            ) > (current.to_dict()["observed_at"], current.digest):
                latest[item.dependency_ref] = item
        return latest

    def _write_once(self, observation: ExternalDependencyObservation) -> None:
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        path = self.observations_dir / f"{observation.digest.removeprefix('sha256:')}.json"
        payload = json.dumps(observation.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != payload:
                raise EvidenceFreshnessError("external observation digest collision")
            return
        temporary = path.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(payload, encoding="utf-8")
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def build_reverification_claim(
    previous: EvidenceClaim,
    *,
    observation: ExternalDependencyObservation,
    issued_at: str,
    result: str,
    suite_digest: str,
    evidence_digest: str,
    provenance: Mapping[str, Any],
) -> EvidenceClaim:
    """Emit a new claim; never mutate the historically verified source claim."""

    value = previous.to_dict()
    observed = observation.to_dict()
    dependencies: list[dict[str, Any]] = []
    replaced = False
    for dependency in value["dependencies"]:
        item = dict(dependency)
        if item["ref"] == observed["dependency_ref"]:
            item = {"ref": observed["dependency_ref"]}
            if observed.get("observed_version"):
                item["observed_version"] = observed["observed_version"]
            if observed.get("fingerprint"):
                item["fingerprint"] = observed["fingerprint"]
            replaced = True
        dependencies.append(item)
    if not replaced:
        raise EvidenceFreshnessError("observation does not match a dependency in the source claim")
    seed = canonical_payload_digest(
        {
            "previous_claim_digest": previous.digest,
            "observation_digest": observation.digest,
            "issued_at": issued_at,
            "result": result,
            "evidence_digest": evidence_digest,
        }
    ).removeprefix("sha256:")[:24]
    return EvidenceClaim.create(
        claim_ref=f"evidence-claim:reverify/{seed}",
        claim_kind=value["claim_kind"],
        subjects=value["subjects"],
        environment=value["environment"],
        dependencies=dependencies,
        suite_digest=suite_digest,
        evidence_digest=evidence_digest,
        provenance=provenance,
        issued_at=issued_at,
        freshness=value["freshness"],
        result=result,
        redaction=value["redaction"],
        portability_scope=value["portability_scope"],
    )


def dependency_evidence_impact(
    dependency_ref: str,
    *,
    claims: Iterable[EvidenceClaim],
    resolutions: Iterable[Mapping[str, Any]] = (),
    plans: Iterable[Mapping[str, Any]] = (),
    applications: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a rebuildable dependency-to-installation impact projection."""

    affected_claims = [
        item
        for item in claims
        if any(
            str(dependency.get("ref")) == dependency_ref
            for dependency in item.to_dict()["dependencies"]
        )
    ]
    claim_digests = {item.digest for item in affected_claims}
    affected_resolutions = [
        dict(item)
        for item in resolutions
        if any(
            str(evidence.get("claim_digest")) in claim_digests
            for evidence in item.get("evidence") or []
        )
    ]
    resolution_digests = {
        str(item.get("resolution_digest") or item.get("application_resolution_digest") or "")
        for item in affected_resolutions
    }
    affected_plans = [
        dict(item)
        for item in plans
        if str(item.get("application_resolution_digest") or "") in resolution_digests
        or any(str(entry.get("claim_digest")) in claim_digests for entry in item.get("evidence") or [])
    ]
    affected_applications = [
        dict(item)
        for item in applications
        if str(item.get("application_resolution_digest") or item.get("resolution_digest") or "")
        in resolution_digests
    ]
    return {
        "schema": "adaos.evidence.dependency_impact.v1",
        "dependency_ref": dependency_ref,
        "claims": [
            {
                "claim_ref": item.claim_ref,
                "claim_digest": item.digest,
                "subjects": item.to_dict()["subjects"],
            }
            for item in affected_claims
        ],
        "resolutions": affected_resolutions,
        "plans": affected_plans,
        "applications": affected_applications,
        "rebuildable": True,
    }


__all__ = [
    "EvidenceAssessmentResult",
    "EvidenceAssessmentService",
    "EvidenceFreshnessError",
    "ExternalChangeMonitorStore",
    "build_reverification_claim",
    "dependency_evidence_impact",
]
