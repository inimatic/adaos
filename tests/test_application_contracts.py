from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.application import (
    Application,
    ApplicationContractError,
    ApplicationInstallation,
    ApplicationOperation,
    ApplicationRelease,
    ApplicationSubscription,
    RuntimeSelection,
    TrialAccessGrant,
)
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
    StableSubscription,
    canonical_payload_digest,
)


ABI_ROOT = Path(__file__).parents[1] / "src" / "adaos" / "abi"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/recipes",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )


def _project_release() -> ProjectRelease:
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version="1.0.0",
        digest=DIGEST_A,
        manifest_digest=DIGEST_B,
        source_ref=_source(),
    )
    return ProjectRelease(
        project_id="recipes",
        version="1.0.0",
        source_ref=_source(),
        components=(package,),
        validation_evidence=({"status": "passed", "suite": "trial"},),
    ).seal()


def _application() -> Application:
    return Application(
        application_id="app_recipes",
        legacy_project_id="recipes",
        publisher_ref="subnet:sn_home",
        slug="recipes",
        display={"title": "Recipes", "summary": "Shared recipes"},
        visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:recipes"},),
        publisher={
            "publisher_ref": "subnet:sn_home",
            "display_name": "Home subnet",
            "subnet_short_ref": "sn_home",
            "release_key_ref": "subnet-key:release-signing:1",
            "release_key_fingerprint": DIGEST_C,
            "home_zone": "local-dev",
            "trust_relation": "local",
        },
    )


def _contracts() -> list[tuple[str, object]]:
    release = ApplicationRelease(
        application_id="app_recipes",
        publisher_ref="subnet:sn_home",
        project_release=_project_release(),
        accepted_candidate_id="candidate.recipes.1",
        acceptance_evidence=({"decision": "accepted", "actor": "owner"},),
        provenance_refs=(DIGEST_C,),
        lifecycle="stable",
        published_at="2026-09-05T12:00:00+00:00",
    )
    installation = ApplicationInstallation(
        installation_id="installation:recipes",
        application_id="app_recipes",
        installed_release_digest=release.release_digest,
        component_refs=({"component_ref": "scenario:recipes", "package_digest": DIGEST_A, "lifecycle": "bound"},),
        data_policy="retain",
        status="active",
        revision=1,
    )
    subscription = ApplicationSubscription(
        application_id="app_recipes",
        update_track="prerelease",
        update_policy="notify",
        observed_release_digest=release.release_digest,
        revision=1,
    )
    selection = RuntimeSelection(
        webspace_id="desktop",
        application_id="app_recipes",
        source="stable_installation",
        release_digest=release.release_digest,
        runtime_root_ref="workspace",
        revision=1,
    )
    grant = TrialAccessGrant(
        grant_id="grant:recipes:guest",
        application_id="app_recipes",
        publisher_ref="subnet:sn_home",
        scope="exact_release",
        release_digest=release.release_digest,
        recipient_subnet_ref="subnet:sn_guest",
        recipient_key_ref="subnet-key:trial-delivery:1",
        expires_at="2026-10-05T12:00:00+00:00",
        max_uses=2,
        uses=0,
        nonce="nonce-recipes-guest-1",
        allowed_zones=("local-dev",),
        status="active",
        revision=1,
    )
    plan = {"kind": "install", "release_digest": release.release_digest}
    operation = ApplicationOperation(
        operation_id="operation:install:recipes",
        application_id="app_recipes",
        kind="install",
        status="planned",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        plan_digest=canonical_payload_digest(plan),
        idempotency_key="install-recipes-1",
        expected_revision=0,
        revision=1,
        plan=plan,
    )
    return [
        ("application.v1.schema.json", _application()),
        ("application.release.v1.schema.json", release),
        ("application.installation.v1.schema.json", installation),
        ("application.subscription.v1.schema.json", subscription),
        ("application.runtime-selection.v1.schema.json", selection),
        ("application.trial-access-grant.v1.schema.json", grant),
        ("application.operation.v1.schema.json", operation),
    ]


def test_application_contracts_round_trip_and_validate_against_abi() -> None:
    for schema_name, contract in _contracts():
        payload = contract.to_dict()
        schema = json.loads((ABI_ROOT / schema_name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(payload)
        assert type(contract).from_mapping(payload) == contract


def test_application_release_preserves_legacy_project_release_digest() -> None:
    project_release = _project_release()
    application_release = ApplicationRelease(
        application_id="app_recipes",
        publisher_ref="subnet:sn_home",
        project_release=project_release,
        accepted_candidate_id="candidate.recipes.1",
        acceptance_evidence=({"decision": "accepted"},),
        provenance_refs=(DIGEST_C,),
    )

    assert application_release.release_digest == project_release.release_digest
    assert application_release.to_dict()["project_release"] == project_release.to_dict()


def test_subscription_compatibility_keeps_track_and_observed_digest() -> None:
    legacy = StableSubscription(
        project_id="recipes",
        channel="prerelease",
        policy="notify",
        installed_digest=DIGEST_A,
    )

    current = ApplicationSubscription.from_legacy("app_recipes", legacy)

    assert current.update_track == "prerelease"
    assert current.to_legacy("recipes") == legacy


def test_runtime_selection_compare_and_swap_is_explicit() -> None:
    selection = dict(_contracts())["application.runtime-selection.v1.schema.json"]

    advanced = selection.advance(
        expected_revision=1,
        source="local_trial",
        runtime_root_ref="trial:candidate.recipes.2",
    )

    assert advanced.revision == 2
    with pytest.raises(ApplicationContractError, match="revision conflict"):
        advanced.advance(expected_revision=1)


def test_application_operation_rejects_unreviewed_plan_content() -> None:
    plan = {"kind": "install", "release_digest": DIGEST_A}
    with pytest.raises(ApplicationContractError, match="plan_digest"):
        ApplicationOperation(
            operation_id="operation:install:recipes",
            application_id="app_recipes",
            kind="install",
            status="planned",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            plan_digest=DIGEST_B,
            idempotency_key="install-recipes-1",
            expected_revision=0,
            revision=1,
            plan=plan,
        )


def test_application_identity_does_not_collapse_into_legacy_project_id() -> None:
    payload = _application().to_dict()
    payload["publisher_ref"] = "project:recipes"

    with pytest.raises(ApplicationContractError, match="publisher_ref"):
        Application.from_mapping(payload)
