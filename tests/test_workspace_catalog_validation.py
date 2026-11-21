import json
from pathlib import Path

import pytest

from adaos.services.workspace_catalog_validation import canonical_digest, validate, retain_test_proof


def proof_for(project='example', version='1.2.3', digest=None, package=None, revision=None):
    proof = {'schema':'adaos.application.catalog_test_proof.v1', 'status':'passed',
        'project_id':project, 'version':version, 'release_digest':digest or 'sha256:'+'a'*64,
        'source_branch':'main', 'source_revision':revision or 'c'*40,
        'package_digests':[package or 'sha256:'+'b'*64],
        **{key:'sha256:'+'e'*64 for key in ('source_snapshot_digest','environment_digest',
                                           'candidate_acceptance_digest','test_receipt_digest')},
        'test_plan':{'schema':'adaos.project.test_plan.v1','complete_suite':True,'suites':['project:'+project]},
        'packages':[{'ref':'project:'+project,'log_sha256':'f'*64,'statuses':['passed']}]}
    proof['proof_digest'] = canonical_digest(proof)
    return proof


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def catalog(tmp_path):
    digest = "sha256:" + "a" * 64
    package = "sha256:" + "b" * 64
    record = {"schema": "adaos.semantic_registry.application_release.v1", "project_id": "example",
        "version": "1.2.3", "project_release_digest": digest}
    record["projection_digest"] = canonical_digest(record)
    write(tmp_path / "semantic/applications/example/release.json", record)
    binding = {"project_release_digest": digest, "path": "semantic/applications/example/release.json",
               "projection_digest": record["projection_digest"]}
    write(tmp_path / "semantic/index.json", {"application_releases": {"project:example@" + digest: binding}})
    pointer = {"release": "example@1.2.3", "version": "1.2.3", "release_digest": digest,
        "package_digest": package, "source_revision": "c" * 40}
    entry = {"kind": "project", "id": "example", "name": "example", "version": "1.2.3",
        "path": "projects/example", "manifest": "projects/example/project.yaml", "channels": {"stable": pointer}}
    write(tmp_path / "registry.json", {"version": 2, "skills": [], "scenarios": [], "projects": [entry]})
    retain_test_proof(tmp_path,proof_for())
    return tmp_path, entry, record


def test_verified_release_can_be_retained_without_canonical_source(catalog):
    root, _, _ = catalog
    assert validate(root)["ok"] is True


def test_newer_source_does_not_relabel_the_published_stable(catalog):
    root, _, _ = catalog
    manifest = root / "projects/example/project.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("id: example\nversion: 9.9.9\n", encoding="utf-8")
    assert validate(root)["ok"] is True
    assert json.loads((root / "registry.json").read_text())["projects"][0]["version"] == "1.2.3"


def test_tampered_projection_never_counts_as_retained_distribution(catalog):
    root, _, record = catalog
    record["version"] = "9.9.9"
    write(root / "semantic/applications/example/release.json", record)
    result = validate(root)
    assert not result["ok"]
    assert any("binding_mismatch" in e["code"] for e in result["errors"])


def test_missing_source_without_a_verified_distribution_is_quarantined_with_its_metadata(catalog):
    root, entry, _ = catalog
    (root / "semantic/index.json").unlink()
    assert not validate(root)["ok"]
    result = validate(root, retire_unavailable=True)
    assert result["ok"] and result["retired_count"] == 1
    data = json.loads((root / "registry.json").read_text())
    assert data["projects"] == []
    assert data["retired_entries"][0]["entry"] == entry
    before = (root / "registry.json").read_bytes()
    assert validate(root, retire_unavailable=True)["retired_count"] == 0
    assert (root / "registry.json").read_bytes() == before


def test_inconsistent_catalog_is_not_partially_repaired(catalog):
    root, entry, _ = catalog
    (root / "semantic/index.json").unlink()
    entry["version"] = "invalid"
    write(root / "registry.json", {"version": 2, "skills": [], "scenarios": [], "projects": [entry]})
    before = (root / "registry.json").read_bytes()
    assert not validate(root, retire_unavailable=True)["ok"]
    assert (root / "registry.json").read_bytes() == before


def test_path_escape_is_rejected_before_any_repair(catalog):
    root, entry, _ = catalog
    entry["manifest"] = "../private/project.yaml"
    write(root / "registry.json", {"version": 2, "skills": [], "scenarios": [], "projects": [entry]})
    with pytest.raises(ValueError, match="unsafe_catalog_path"):
        validate(root, retire_unavailable=True)


def test_public_application_catalog_repairs_missing_project_projection_idempotently(catalog):
    root, entry, _ = catalog
    digest = entry["channels"]["stable"]["release_digest"]
    project = {"project_id": "example", "version": "1.2.3", "release_digest": digest,
        "source_ref": {"revision": "c" * 40},
        "components": [{"kind": "scenario", "artifact_id": "example", "digest": "sha256:" + "b" * 64}]}
    envelope = {"release": {"application_id": "app_example", "release_digest": digest,
        "project_release": project}, "channels": {"stable": digest}}
    envelope["projection_digest"] = canonical_digest(envelope)
    write(root / "semantic/catalog/releases/example.json", envelope)
    write(root / "semantic/catalog/index.json", {"applications": {"app_example": {
        "stable_release_digest": digest, "release_key": "example"}}, "releases": {"example": {
            "release_digest": digest, "path": "semantic/catalog/releases/example.json",
            "projection_digest": envelope["projection_digest"]}}})
    write(root / "registry.json", {"version": 2, "skills": [], "scenarios": [], "projects": []})
    assert not validate(root)["ok"]
    assert validate(root, synchronize_published=True)["ok"]
    before = (root / "registry.json").read_bytes()
    assert validate(root, synchronize_published=True)["ok"]
    assert (root / "registry.json").read_bytes() == before


def test_registry_ci_projection_uses_the_same_core_validator_when_checkout_is_available():
    canonical = Path(__file__).parents[1] / "src/adaos/services/workspace_catalog_validation.py"
    vendored = canonical.parents[4] / "adaos-registry/.github/scripts/validate_registry.py"
    if vendored.is_file():
        assert vendored.read_bytes() == canonical.read_bytes()


def test_new_channel_requires_complete_exact_release_proof(catalog):
    root, entry, _ = catalog
    path = next((root/'semantic/test-acceptance').iterdir())
    proof = json.loads(path.read_text())
    path.unlink()
    assert any('tests_unverified' in error['code'] for error in validate(root)['errors'])
    for fault in ('missing_suite','failed_result','wrong_source','wrong_package','tampered'):
        changed = json.loads(json.dumps(proof))
        if fault == 'missing_suite': changed['test_plan']['suites'].append('skill:missing')
        elif fault == 'failed_result': changed['packages'][0]['statuses'] = ['failed']
        elif fault == 'wrong_source': changed['source_revision'] = 'd'*40
        elif fault == 'wrong_package': changed['package_digests'] = ['sha256:'+'d'*64]
        else: changed['status'] = 'failed'
        if fault != 'tampered':
            changed['proof_digest'] = canonical_digest({k:v for k,v in changed.items() if k != 'proof_digest'})
        write(path,changed)
        assert not validate(root)['ok'], fault
    write(path,proof)
    assert validate(root)['ok']


def test_only_unchanged_historical_channel_can_use_previous_revision(catalog):
    import subprocess
    root, entry, _ = catalog
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.com',
        'commit','-m','Historical catalog'],check=True,capture_output=True)
    next((root/'semantic/test-acceptance').iterdir()).unlink()
    assert validate(root,base_ref='HEAD')['ok']
    assert not validate(root)['ok']
    entry['channels']['stable']['package_digest'] = 'sha256:'+'d'*64
    write(root/'registry.json',{'version':2,'skills':[],'scenarios':[],'projects':[entry]})
    assert not validate(root,base_ref='HEAD')['ok']
    with pytest.raises(ValueError,match='unavailable'):
        validate(root,base_ref='a'*40)
