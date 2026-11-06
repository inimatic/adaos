from __future__ import annotations

from pathlib import Path
import shutil

from adaos.services.public_site_components import PublicSiteComponentRegistry


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "src/adaos/integrations/adaos-client/src/assets/public-sites/adaos"
)


def _site(tmp_path: Path) -> Path:
    target = tmp_path / "site"
    shutil.copytree(FIXTURE, target)
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
