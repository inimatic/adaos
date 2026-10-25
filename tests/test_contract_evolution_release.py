"""Exact package bytes -> admitted release -> correctly owned compatibility work."""

from dataclasses import replace

import pytest
import yaml

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactSourceRef, ProjectRelease
from adaos.services.applications.store import ApplicationStore
from adaos.services.artifact_pipeline.packages import build_artifact_package
from adaos.services.contract_evolution.producers import current_contracts, unit_digest
from adaos.services.contract_evolution.records import digest, seal
from adaos.services.contract_evolution.service import ContractEvolutionService, USAGE
from adaos.services.contract_evolution.usage import package_usage_evidence


CONTRACT = "sdk:adaos.sdk.applications.get_application"


def release_fixture(tmp_path, *, scenario_uses_sdk=False):
    packages = []
    for kind, name in (("scenario", "viewer"), ("skill", "worker")):
        root = tmp_path / "source" / kind / name
        root.mkdir(parents=True)
        manifest = {"id": name, "name": name, "version": "1.0.0"}
        (root / (kind + ".yaml")).write_text(yaml.safe_dump(manifest), encoding="utf-8")
        if kind == "skill" or scenario_uses_sdk:
            (root / "handler.py").write_text(
                "from adaos.sdk import applications\napplications.get_application('demo')\n", encoding="utf-8")
        source = ArtifactSourceRef(forge="local", repository="cef-test", revision="a" * 40,
                                   path_scope=(kind + "s/" + name + "/",))
        packages.append(build_artifact_package(root, kind=kind, source_ref=source))
    evidence = package_usage_evidence(packages, project_id="demo")
    project = ProjectRelease(project_id="demo", version="1.0.0", source_ref=packages[0].ref.source_ref,
        components=tuple(item.ref for item in packages), validation_evidence=evidence).seal()
    release = ApplicationRelease(application_id="demo", publisher_ref="subnet:sn_home", project_release=project,
        accepted_candidate_id="candidate.cef", acceptance_evidence=({"decision": "accepted"},),
        provenance_refs=(digest("fixture-provenance"),), lifecycle="trial")
    store = ApplicationStore(tmp_path / "state")
    store.save_application(Application(application_id="demo", legacy_project_id="demo", publisher_ref="subnet:sn_home",
        slug="demo", display={"title": "Demo"}, visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:viewer"},),
        publisher={"publisher_ref": "subnet:sn_home", "display_name": "Home", "subnet_short_ref": "sn_home",
                   "release_key_ref": "subnet-key:release-signing:1", "release_key_fingerprint": digest("key"),
                   "home_zone": "local-dev", "trust_relation": "local"}), expected_revision=0)
    return store, release


def publish_delta(service, classification="breaking"):
    return service.publish_change(contract_ref=CONTRACT, contract_kind="sdk_operation", owner_ref="core:sdk",
        before=unit_digest(current_contracts()[CONTRACT]), after=digest("tightened"), classification=classification)


def reseal(record, **changes):
    return seal(record["schema"], **{**{key: value for key, value in record.items()
        if key not in {"schema", "digest", "recorded_at", "usage_lock_id"}}, **changes})


def test_release_admission_indexes_bytes_and_routes_skill_debt_to_owning_project(tmp_path):
    store, release = release_fixture(tmp_path)
    service = ContractEvolutionService(store.state_dir)
    publish_delta(service)
    store.put_release(release)
    usage = service.store.list(USAGE)[0]
    assert usage["consumer_digest"] == release.release_digest
    assert usage["consumer_ref"] == "project:demo"
    assert usage["edges"][0]["component_refs"] == ["skill:worker"]
    assert usage["edges"][0]["surface"][0].startswith("skill:worker/")
    assessment = service.application_projection("application:demo", consumer_digest=release.release_digest)["assessments"][0]
    ticket = service.materialize_ticket(assessment["assessment_id"])
    assert ticket["target_scope"]["type"] == "project"
    assert ticket["target_scope"]["id"] == "demo"
    assert ticket["metadata"]["builder_repair"]["target_object_id"] == "worker"
    assert ticket["metadata"]["contract_evolution"]["affected_components"] == ["skill:worker"]
    store.put_release(release)
    assert len(service.store.list(USAGE)) == 1


def test_multi_component_usage_preserves_all_used_surfaces(tmp_path):
    store, release = release_fixture(tmp_path, scenario_uses_sdk=True)
    service = ContractEvolutionService(store.state_dir)
    publish_delta(service)
    store.put_release(release)
    edge = service.store.list(USAGE)[0]["edges"][0]
    assert edge["component_refs"] == ["scenario:viewer", "skill:worker"]
    assert {path.split("/", 1)[0] for path in edge["surface"]} == set(edge["component_refs"])


def test_missing_component_usage_cannot_become_alignment_proof(tmp_path):
    store, release = release_fixture(tmp_path)
    service = ContractEvolutionService(store.state_dir)
    publish_delta(service, "additive")
    project = replace(release.project_release, validation_evidence=release.project_release.validation_evidence[1:],
                      validation_evidence_refs=(), release_digest=None).seal()
    store.put_release(replace(release, project_release=project))
    usage = service.store.list(USAGE)[0]
    assert not usage["complete"]
    assert {item["component_ref"] for item in usage["unresolved"] if item["code"] == "component_usage_missing"} == {"scenario:viewer"}
    assert service.application_projection("application:demo", consumer_digest=project.release_digest)["posture"] == "verification_due"


def test_wrong_package_evidence_is_rejected_before_release_persistence(tmp_path):
    store, release = release_fixture(tmp_path)
    evidence = release.project_release.validation_evidence
    project = replace(release.project_release,
        validation_evidence=(reseal(evidence[0], consumer_digest=digest("wrong-package")), *evidence[1:]),
        validation_evidence_refs=(), release_digest=None).seal()
    with pytest.raises(ValueError, match="exact release component"):
        store.put_release(replace(release, project_release=project))
    with pytest.raises(FileNotFoundError):
        store.get_release("demo", project.release_digest)


def test_conflicting_component_contracts_remain_unknown(tmp_path):
    store, release = release_fixture(tmp_path, scenario_uses_sdk=True)
    service = ContractEvolutionService(store.state_dir)
    publish_delta(service)
    evidence = release.project_release.validation_evidence
    altered = reseal(evidence[0], edges=[{**edge, "contract_digest": digest("different-baseline")} for edge in evidence[0]["edges"]])
    project = replace(release.project_release, validation_evidence=(altered, *evidence[1:]),
                      validation_evidence_refs=(), release_digest=None).seal()
    store.put_release(replace(release, project_release=project))
    posture = service.application_projection("application:demo", consumer_digest=project.release_digest)
    assert posture["posture"] == "verification_due"
    assert posture["assessments"][0]["reason"] == "ComponentContractDigestConflict"
    assert posture["ticket_count"] == 0
