from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, ProjectRelease
from adaos.services.artifact_pipeline import (
    ArtifactAttestation,
    ArtifactAttestationPublisher,
    AttestationPublicationConflict,
    AttestationPublicationError,
    AttestationPublicationUncertain,
    Ed25519ArtifactSigner,
    build_artifact_package,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )


def _plan(root: Path, *, version: str = "1.2.3") -> ReleasePlan:
    scenario = root / f"recipes-{version}"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        f"id: recipes\nversion: {version}\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text('{"ui": {}}\n', encoding="utf-8")
    built = build_artifact_package(scenario, kind="scenario", source_ref=_source())
    release = ProjectRelease(
        project_id="recipes",
        version=version,
        source_ref=_source(),
        components=(built.ref,),
        validation_evidence=({"suite": "scenario", "status": "passed"},),
    ).seal()
    return ReleasePlan(
        release=release,
        packages=(built.ref,),
        bindings=(),
        reverse_consumers={},
    )


class _OutcomeStore:
    def __init__(self, *, first_outcome: str = "acknowledge") -> None:
        self.first_outcome = first_outcome
        self.put_calls = 0
        self.list_calls = 0
        self.assets: dict[tuple[str, str], list[ArtifactAttestation]] = {}

    def put(self, attestation: ArtifactAttestation) -> str:
        self.put_calls += 1
        sealed = attestation.seal()
        if self.put_calls == 1 and self.first_outcome == "commit_then_raise":
            self.assets.setdefault(
                (sealed.subject_kind, sealed.subject_digest), []
            ).append(sealed)
            raise TimeoutError("remote acknowledgement was lost")
        if self.put_calls == 1 and self.first_outcome == "raise_without_commit":
            raise TimeoutError("remote write outcome is unknown")
        members = self.assets.setdefault((sealed.subject_kind, sealed.subject_digest), [])
        if sealed not in members:
            members.append(sealed)
        return str(sealed.attestation_digest)

    def list_for_subject(self, subject_kind, subject_digest):
        self.list_calls += 1
        return tuple(self.assets.get((subject_kind, subject_digest), ()))


def _publisher(root: Path, store: _OutcomeStore) -> ArtifactAttestationPublisher:
    return ArtifactAttestationPublisher(
        state_root=root,
        store=store,
        signer=Ed25519ArtifactSigner.generate(issuer="inimatic.release"),
        clock=lambda: datetime(2026, 7, 27, 9, 30, tzinfo=timezone.utc),
    )


def test_publish_writes_exact_set_once_and_repeat_is_idempotent(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "source")
    store = _OutcomeStore()
    publisher = _publisher(tmp_path / "state", store)

    first = publisher.publish(plan, idempotency_key="release-recipes-1.2.3")
    second = publisher.publish(plan, idempotency_key="release-recipes-1.2.3")

    assert first == second
    assert first.status == "completed"
    assert [item.subject_kind for item in first.attestations] == ["package", "release"]
    assert all(item.status == "completed" for item in first.attestations)
    assert store.put_calls == 2


def test_commit_then_timeout_requires_read_reconciliation_before_explicit_resume(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path / "source")
    store = _OutcomeStore(first_outcome="commit_then_raise")
    publisher = _publisher(tmp_path / "state", store)
    key = "release-recipes-1.2.3"

    with pytest.raises(AttestationPublicationUncertain) as failure:
        publisher.publish(plan, idempotency_key=key)
    operation_id = failure.value.operation_id
    operation_path = publisher.operation_path(operation_id)
    before = json.loads(operation_path.read_text(encoding="utf-8"))
    signed_before = [item["attestation"] for item in before["items"]]

    with pytest.raises(AttestationPublicationUncertain):
        publisher.publish(plan, idempotency_key=key)
    assert store.put_calls == 1

    reconciled = publisher.reconcile(operation_id)
    assert reconciled.status == "ready"
    assert reconciled.attestations[0].reconciled is True
    assert store.put_calls == 1
    assert store.list_calls == 1

    completed = publisher.publish(plan, idempotency_key=key)
    after = json.loads(operation_path.read_text(encoding="utf-8"))
    assert completed.status == "completed"
    assert store.put_calls == 2
    assert [item["attestation"] for item in after["items"]] == signed_before


def test_absent_asset_keeps_unknown_outcome_blocked_without_retry(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "source")
    store = _OutcomeStore(first_outcome="raise_without_commit")
    publisher = _publisher(tmp_path / "state", store)
    key = "release-recipes-1.2.3"

    with pytest.raises(AttestationPublicationUncertain) as failure:
        publisher.publish(plan, idempotency_key=key)
    reconciled = publisher.reconcile(failure.value.operation_id)

    assert reconciled.status == "uncertain"
    assert reconciled.attestations[0].status == "uncertain"
    assert store.put_calls == 1
    assert store.list_calls == 1
    with pytest.raises(AttestationPublicationUncertain):
        publisher.publish(plan, idempotency_key=key)
    assert store.put_calls == 1


def test_idempotency_key_cannot_be_rebound_to_a_different_plan(tmp_path: Path) -> None:
    first = _plan(tmp_path / "first", version="1.2.3")
    second = _plan(tmp_path / "second", version="1.2.4")
    store = _OutcomeStore()
    publisher = _publisher(tmp_path / "state", store)

    publisher.publish(first, idempotency_key="same-release-request")
    with pytest.raises(AttestationPublicationConflict, match="different release plan"):
        publisher.publish(second, idempotency_key="same-release-request")
    assert store.put_calls == 2


def test_dispatching_after_crash_is_never_retried_implicitly(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "source")
    store = _OutcomeStore()
    publisher = _publisher(tmp_path / "state", store)
    key = "release-recipes-1.2.3"
    idempotency_digest = publisher._idempotency_digest(key)
    operation_id = publisher.operation_id(key)
    operation = publisher._create(
        plan,
        operation_id=operation_id,
        idempotency_digest=idempotency_digest,
    )
    operation["items"][0]["status"] = "dispatching"
    operation["status"] = "publishing"
    publisher._save(operation)

    with pytest.raises(AttestationPublicationUncertain):
        publisher.publish(plan, idempotency_key=key)
    assert store.put_calls == 0


def test_journal_tampering_is_rejected_before_external_mutation(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "source")
    store = _OutcomeStore(first_outcome="raise_without_commit")
    publisher = _publisher(tmp_path / "state", store)
    key = "release-recipes-1.2.3"

    with pytest.raises(AttestationPublicationUncertain) as failure:
        publisher.publish(plan, idempotency_key=key)
    path = publisher.operation_path(failure.value.operation_id)
    operation = json.loads(path.read_text(encoding="utf-8"))
    operation["items"][0]["status"] = "pending"
    path.write_text(json.dumps(operation), encoding="utf-8")

    with pytest.raises(AttestationPublicationError, match="journal digest mismatch"):
        publisher.publish(plan, idempotency_key=key)
    assert store.put_calls == 1


def test_inconsistent_release_plan_is_rejected_before_sign_or_write(tmp_path: Path) -> None:
    valid = _plan(tmp_path / "source")
    malformed = ReleasePlan(
        release=valid.release,
        packages=(),
        bindings=(),
        reverse_consumers={},
    )
    store = _OutcomeStore()
    publisher = _publisher(tmp_path / "state", store)

    with pytest.raises(AttestationPublicationError, match="internally consistent"):
        publisher.publish(malformed, idempotency_key="malformed-plan")
    assert store.put_calls == 0
    assert not (tmp_path / "state" / "attestation-publications").exists()
