import json
from pathlib import Path

import pytest

from adaos.services.workspace_catalog_validation import canonical_digest, validate


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
