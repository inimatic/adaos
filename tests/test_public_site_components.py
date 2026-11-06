from __future__ import annotations

from pathlib import Path
import json
import shutil

from adaos.services.public_site_components import PublicSiteComponentRegistry
from adaos.services.site_studio.contracts import digest


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "src/adaos/integrations/adaos-client/src/assets/public-sites/adaos"
)


def _site(tmp_path: Path) -> Path:
    target = tmp_path / "site"
    shutil.copytree(FIXTURE, target)
    return target


def _equivalent_candidate(tmp_path: Path) -> Path:
    target = tmp_path / "candidate"
    shutil.copytree(FIXTURE, target)
    release_path = target / "components/release-set.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release.pop("set_digest")
    release["set_id"] = "site-set-adaos-static-r2"
    release["qualified_at"] = "2026-11-06T12:30:00+00:00"
    release["set_digest"] = digest(release)
    release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")

    deployment_path = target / "site.deployment.json"
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    deployment.pop("deployment_digest")
    deployment["deployment_id"] = "adaos:components:r2"
    deployment["release_id"] = "components-r2"
    deployment["component_release_set"] = {
        "id": release["set_id"],
        "digest": release["set_digest"],
    }
    deployment["created_at"] = "2026-11-06T12:30:00+00:00"
    deployment["deployment_digest"] = digest(deployment)
    deployment_path.write_text(
        json.dumps(deployment, indent=2) + "\n", encoding="utf-8"
    )
    return target


def test_registry_admits_immutable_families_and_accounts_for_deployments(
    tmp_path: Path,
) -> None:
    registry = PublicSiteComponentRegistry(state_dir=tmp_path / "root")
    admitted = registry.admit_bundle(
        _site(tmp_path),
        actor="skill:site_studio",
        operation_id="prepare-1",
    )
    deployment = admitted["deployment"]

    assert admitted["set_id"] == "site-set-adaos-static-r1"
    assert {item["family"] for item in admitted["packages"]} == {
        "site.core",
        "site.collections",
        "site.media",
    }
    assert admitted["support_state"] == "candidate"

    status = registry.record_publication(
        site_id="adaos",
        project_id="adaos",
        deployment_digest=deployment["deployment_digest"],
        component_set_digest=admitted["set_digest"],
        projection_digest=deployment["projection_digest"],
        renderer_abi=deployment["renderer_abi"],
        actor="skill:site_studio",
        operation_id="publish-1",
        published_at="2026-11-06T12:00:00+00:00",
        acceptance_receipt={"actor": "user:owner", "confirmed": True},
    )

    assert status["current_set"]["support_state"] == "recommended"
    assert status["current_set"]["deployment_count"] == 1
    assert status["upgrade_available"] is False
    assert status["acceptance_history"] == [
        {"actor": "user:owner", "confirmed": True}
    ]


def test_registry_retains_support_policy_and_client_qualification(tmp_path: Path) -> None:
    registry = PublicSiteComponentRegistry(state_dir=tmp_path / "root")
    admitted = registry.admit_bundle(
        _site(tmp_path), actor="root:release", operation_id="prepare-1"
    )
    set_digest = admitted["set_digest"]

    policy = registry.set_support_state(
        set_digest,
        support_state="supported",
        actor="root:release",
        reason="Qualified with the current public Client.",
        recommended=True,
        security_notices=["No active notices."],
    )
    qualification = registry.record_client_qualification(
        client_release_id="site-client-0.0.500-test",
        client_release_digest="sha256:" + "a" * 64,
        set_digest=set_digest,
        renderer_abi="adaos.public-renderer.v1",
        evidence_digest="sha256:" + "b" * 64,
        checks=(
            "contract",
            "routes",
            "responsive",
            "accessibility",
            "visual",
            "style_isolation",
        ),
        actor="root:release",
    )

    observed = registry.release_set_status(set_digest)
    assert policy["support_state"] == "recommended"
    assert observed["recommended"] is True
    assert observed["client_qualifications"] == [qualification]
    state = registry._read()
    assert len(state["packages"]) == 3
    assert [item["action"] for item in state["receipts"]] == [
        "admit_bundle",
        "set_support_state",
        "record_client_qualification",
    ]


def test_registry_previews_and_accepts_exact_component_upgrade(tmp_path: Path) -> None:
    registry = PublicSiteComponentRegistry(state_dir=tmp_path / "root")
    current = registry.admit_bundle(
        _site(tmp_path), actor="root:release", operation_id="prepare-current"
    )
    deployment = current["deployment"]
    registry.record_publication(
        site_id="adaos",
        project_id="adaos",
        deployment_digest=deployment["deployment_digest"],
        component_set_digest=current["set_digest"],
        projection_digest=deployment["projection_digest"],
        renderer_abi=deployment["renderer_abi"],
        actor="skill:site_studio",
        operation_id="publish-current",
        published_at="2026-11-06T12:00:00+00:00",
    )
    candidate = registry.admit_bundle(
        _equivalent_candidate(tmp_path),
        actor="root:release",
        operation_id="prepare-candidate",
    )
    registry.set_support_state(
        candidate["set_digest"],
        support_state="supported",
        recommended=True,
        actor="root:release",
        reason="Equivalent packages retained in a coordinated release set.",
    )

    review = registry.prepare_upgrade("adaos", actor="user:owner")
    preview = registry.upgrade_preview(review["review_id"])
    assert review["status"] == "preview_ready"
    assert review["changed_families"] == []
    assert {item["status"] for item in review["findings"]} == {"passed"}
    assert preview["projection"]["projection_digest"] == deployment["projection_digest"]
    assert preview["component_release_set"]["set_digest"] == candidate["set_digest"]

    accepted = registry.decide_upgrade(
        review["review_id"],
        plan_digest=review["plan_digest"],
        decision="approve",
        actor="user:owner",
    )
    upgraded = accepted["candidate_deployment"]
    assert accepted["status"] == "accepted"
    assert upgraded["projection_digest"] == deployment["projection_digest"]
    assert upgraded["component_release_set"]["digest"] == candidate["set_digest"]
    assert upgraded["rollback_predecessor_digest"] == deployment["deployment_digest"]
    assert upgraded["acceptance"]["kind"] == "owner"


def test_registry_rejection_policy_and_retention_are_explicit(tmp_path: Path) -> None:
    registry = PublicSiteComponentRegistry(state_dir=tmp_path / "root")
    current = registry.admit_bundle(
        _site(tmp_path), actor="root:release", operation_id="prepare-current"
    )
    deployment = current["deployment"]
    registry.record_publication(
        site_id="adaos",
        project_id="adaos",
        deployment_digest=deployment["deployment_digest"],
        component_set_digest=current["set_digest"],
        projection_digest=deployment["projection_digest"],
        renderer_abi=deployment["renderer_abi"],
        actor="skill:site_studio",
        operation_id="publish-current",
        published_at="2026-11-06T12:00:00+00:00",
    )
    candidate = registry.admit_bundle(
        _equivalent_candidate(tmp_path),
        actor="root:release",
        operation_id="prepare-candidate",
    )
    registry.set_support_state(
        candidate["set_digest"],
        support_state="supported",
        recommended=True,
        actor="root:release",
        reason="Qualified candidate.",
    )
    policy = registry.set_upgrade_policy(
        "adaos", mode="explicit_review", actor="user:owner"
    )
    review = registry.prepare_upgrade("adaos", actor="user:owner")
    rejected = registry.decide_upgrade(
        review["review_id"],
        plan_digest=review["plan_digest"],
        decision="reject",
        actor="user:owner",
    )
    retention = registry.retention_plan()

    assert policy["mode"] == "explicit_review"
    assert rejected["status"] == "rejected"
    assert registry.site_status("adaos")["current_set"]["set_digest"] == current["set_digest"]
    assert set(retention["retain_sets"]) == {
        current["set_digest"],
        candidate["set_digest"],
    }
