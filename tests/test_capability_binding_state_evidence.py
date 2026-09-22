from __future__ import annotations

from datetime import datetime, timezone

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import EvidenceClaim
from adaos.services.capability_binding_state import (
    EvidenceAssessmentService,
    ExternalChangeMonitorStore,
    build_reverification_claim,
    dependency_evidence_impact,
)


PROFILE_DIGEST = "sha256:" + "1" * 64
CONTRACT_DIGEST = "sha256:" + "2" * 64
BINDING_DIGEST = "sha256:" + "3" * 64
SUITE_DIGEST = "sha256:" + "4" * 64
EVIDENCE_DIGEST = "sha256:" + "5" * 64


def _claim(
    *,
    issued_at: str = "2026-09-23T00:00:00+00:00",
    result: str = "verified",
    version: str = "3",
    fingerprint: str = "jira-api-v3-a",
) -> EvidenceClaim:
    return EvidenceClaim.create(
        claim_ref=f"evidence-claim:jira/{version}/{result}",
        claim_kind="capability_conformance",
        subjects=(
            {
                "kind": "capability_contract",
                "ref": "capability:external.issues.query",
                "digest": CONTRACT_DIGEST,
            },
            {
                "kind": "binding_definition",
                "ref": "binding-definition:jira.issues.query",
                "digest": BINDING_DIGEST,
            },
        ),
        environment={
            "profile_ref": "profile:local/default",
            "profile_digest": PROFILE_DIGEST,
        },
        dependencies=(
            {
                "ref": "external-api:jira",
                "observed_version": version,
                "fingerprint": fingerprint,
            },
        ),
        suite_digest=SUITE_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        provenance={"issuer": "adaos:conformance", "runner": "pytest", "run_id": f"jira-{version}"},
        issued_at=issued_at,
        freshness={
            "max_age_seconds": 86400,
            "invalidated_by": [
                "dependency_change",
                "environment_change",
                "policy_change",
                "revocation",
            ],
        },
        result=result,
        redaction={"portable": True, "omitted_fields": ["credentials"]},
        portability_scope="portable",
    )


def _policy(*, purpose: str = "production", allow_stale: bool = False) -> dict:
    return {
        "policy_ref": f"evidence-policy:{purpose}",
        "purpose": purpose,
        "allow_stale": allow_stale,
        "max_claim_age_seconds": 86400,
        "max_observation_age_seconds": 3600,
        "trusted_issuers": ["adaos:conformance"],
        "trusted_dependency_domains": ["atlassian:jira-cloud"],
    }


def _subjects() -> dict[str, str]:
    return {
        "capability:external.issues.query": CONTRACT_DIGEST,
        "binding-definition:jira.issues.query": BINDING_DIGEST,
    }


def test_external_change_makes_historical_claim_stale_and_production_inadmissible(tmp_path) -> None:
    events: list[dict] = []
    monitor = ExternalChangeMonitorStore(tmp_path, event_sink=lambda event: events.append(dict(event)))
    original = monitor.observe(
        dependency_ref="external-api:jira",
        observed_version="3",
        fingerprint="jira-api-v3-a",
        observed_at="2026-09-23T00:05:00+00:00",
        trust_domain="atlassian:jira-cloud",
        source={"kind": "external_api", "ref": "https://developer.atlassian.com/cloud/jira"},
        provenance={"observer": "external-change-monitor", "run_id": "monitor-1"},
    )
    claim = _claim()
    assessor = EvidenceAssessmentService(
        now=lambda: datetime(2026, 9, 23, 0, 10, tzinfo=timezone.utc)
    )
    current = assessor.assess(
        claim,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={original.dependency_ref: original},
    )
    assert current.status == "admissible"
    assert current.admissible_for_use is True

    changed = monitor.observe(
        dependency_ref="external-api:jira",
        observed_version="4",
        fingerprint="jira-api-v4-b",
        observed_at="2026-09-23T00:15:00+00:00",
        trust_domain="atlassian:jira-cloud",
        source={"kind": "external_api", "ref": "https://developer.atlassian.com/cloud/jira"},
        provenance={"observer": "external-change-monitor", "run_id": "monitor-2"},
    )
    stale = EvidenceAssessmentService(
        now=lambda: datetime(2026, 9, 23, 0, 20, tzinfo=timezone.utc)
    ).assess(
        claim,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={changed.dependency_ref: changed},
    )
    assert stale.status == "stale"
    assert stale.admissible_for_use is False
    assert stale.explanation["historically_verified"] is True
    assert "historically verified but is stale" in stale.explanation["message"]
    assert monitor.latest("external-api:jira").digest == changed.digest
    assert len(monitor.history("external-api:jira")) == 2
    assert len(events) == 2


def test_simulation_policy_can_surface_stale_evidence_without_changing_status(tmp_path) -> None:
    monitor = ExternalChangeMonitorStore(tmp_path)
    changed = monitor.observe(
        dependency_ref="external-api:jira",
        observed_version="4",
        fingerprint="jira-api-v4-b",
        observed_at="2026-09-23T00:15:00+00:00",
        trust_domain="atlassian:jira-cloud",
        source={"kind": "test", "ref": "fixture:jira-v4"},
        provenance={"observer": "pytest"},
    )
    result = EvidenceAssessmentService(
        now=lambda: datetime(2026, 9, 23, 0, 20, tzinfo=timezone.utc)
    ).assess(
        _claim(),
        policy=_policy(purpose="simulation", allow_stale=True),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={changed.dependency_ref: changed},
    )
    assert result.status == "stale"
    assert result.admissible_for_use is True
    assert result.explanation["purpose"] == "simulation"


def test_reverification_emits_new_verified_or_incompatible_claim(tmp_path) -> None:
    observation = ExternalChangeMonitorStore(tmp_path).observe(
        dependency_ref="external-api:jira",
        observed_version="4",
        fingerprint="jira-api-v4-b",
        observed_at="2026-09-23T00:15:00+00:00",
        trust_domain="atlassian:jira-cloud",
        source={"kind": "test", "ref": "fixture:jira-v4"},
        provenance={"observer": "pytest"},
    )
    previous = _claim()
    verified = build_reverification_claim(
        previous,
        observation=observation,
        issued_at="2026-09-23T00:21:00+00:00",
        result="verified",
        suite_digest=SUITE_DIGEST,
        evidence_digest=canonical_payload_digest({"jira": "v4", "result": "passed"}),
        provenance={"issuer": "adaos:conformance", "runner": "pytest", "run_id": "jira-v4"},
    )
    incompatible = build_reverification_claim(
        previous,
        observation=observation,
        issued_at="2026-09-23T00:22:00+00:00",
        result="incompatible",
        suite_digest=SUITE_DIGEST,
        evidence_digest=canonical_payload_digest({"jira": "v4", "result": "failed"}),
        provenance={"issuer": "adaos:conformance", "runner": "pytest", "run_id": "jira-v4-fail"},
    )
    assessor = EvidenceAssessmentService(
        now=lambda: datetime(2026, 9, 23, 0, 25, tzinfo=timezone.utc)
    )
    verified_result = assessor.assess(
        verified,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={observation.dependency_ref: observation},
    )
    incompatible_result = assessor.assess(
        incompatible,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={observation.dependency_ref: observation},
    )
    historical = assessor.assess(
        previous,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={observation.dependency_ref: observation},
        superseding_claims=(verified,),
    )
    assert verified_result.status == "admissible"
    assert incompatible_result.status == "incompatible"
    assert historical.status == "superseded"
    assert previous.to_dict()["dependencies"][0]["observed_version"] == "3"


def test_revocation_subject_change_and_dependency_impact_are_derived(tmp_path) -> None:
    observation = ExternalChangeMonitorStore(tmp_path).observe(
        dependency_ref="external-api:jira",
        observed_version="3",
        fingerprint="jira-api-v3-a",
        observed_at="2026-09-23T00:05:00+00:00",
        trust_domain="atlassian:jira-cloud",
        source={"kind": "test", "ref": "fixture:jira-v3"},
        provenance={"observer": "pytest"},
    )
    claim = _claim()
    assessor = EvidenceAssessmentService(
        now=lambda: datetime(2026, 9, 23, 0, 10, tzinfo=timezone.utc)
    )
    revoked = assessor.assess(
        claim,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=_subjects(),
        dependency_observations={observation.dependency_ref: observation},
        revoked_claim_digests=(claim.digest,),
    )
    changed_subjects = {**_subjects(), "binding-definition:jira.issues.query": "sha256:" + "9" * 64}
    superseded = assessor.assess(
        claim,
        policy=_policy(),
        environment_profile_digest=PROFILE_DIGEST,
        subject_digests=changed_subjects,
        dependency_observations={observation.dependency_ref: observation},
    )
    resolution = {
        "resolution_digest": "sha256:" + "6" * 64,
        "evidence": [{"claim_digest": claim.digest}],
    }
    plan = {
        "plan_digest": "sha256:" + "7" * 64,
        "application_resolution_digest": resolution["resolution_digest"],
        "evidence": [],
    }
    application = {
        "application_ref": "application:issues",
        "application_resolution_digest": resolution["resolution_digest"],
    }
    impact = dependency_evidence_impact(
        "external-api:jira",
        claims=(claim,),
        resolutions=(resolution,),
        plans=(plan,),
        applications=(application,),
    )
    assert revoked.status == "revoked"
    assert superseded.status == "superseded"
    assert [item["claim_digest"] for item in impact["claims"]] == [claim.digest]
    assert impact["resolutions"] == [resolution]
    assert impact["plans"] == [plan]
    assert impact["applications"] == [application]
    assert impact["rebuildable"] is True
