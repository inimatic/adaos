from __future__ import annotations

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_json_bytes, sha256_digest
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    PackageBuildError,
    PackageVerificationError,
    build_artifact_package,
    verify_artifact_package,
)


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )


def _scenario(root: Path) -> Path:
    scenario = root / "recipes"
    (scenario / "assets").mkdir(parents=True)
    (scenario / "__pycache__").mkdir()
    (scenario / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.2.3\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text('{"ui": {}}\n', encoding="utf-8")
    (scenario / "assets" / "icon.svg").write_text("<svg/>\n", encoding="utf-8")
    (scenario / "builder.draft.json").write_text('{"private": true}\n', encoding="utf-8")
    (scenario / "prompt_state.json").write_text('{"workflow": "prototype"}\n', encoding="utf-8")
    (scenario / "builder_memory.md").write_text("private notes\n", encoding="utf-8")
    (scenario / "tests").mkdir()
    (scenario / "tests" / "test_contract.py").write_text("assert True\n", encoding="utf-8")
    (scenario / "ui_revisions").mkdir()
    (scenario / "ui_revisions" / "001.json").write_text('{}\n', encoding="utf-8")
    (scenario / "__pycache__" / "generated.pyc").write_bytes(b"cache")
    return scenario


def test_package_build_is_deterministic_and_excludes_dev_state(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)

    first = build_artifact_package(scenario, kind="scenario", source_ref=_source())
    os.utime(scenario / "scenario.yaml", (1_900_000_000, 1_900_000_000))
    second = build_artifact_package(scenario, kind="scenario", source_ref=_source())

    assert first.archive_bytes == second.archive_bytes
    assert first.ref.digest == second.ref.digest
    verified = verify_artifact_package(first.archive_bytes, expected_digest=first.ref.digest)
    assert verified.ref == first.ref
    assert "scenario.yaml" in verified.file_names
    assert "builder.draft.json" not in verified.file_names
    assert "prompt_state.json" not in verified.file_names
    assert "builder_memory.md" not in verified.file_names
    assert not any(item.startswith("tests/") for item in verified.file_names)
    assert not any(item.startswith("ui_revisions/") for item in verified.file_names)
    assert not any("__pycache__" in item for item in verified.file_names)


def test_package_digest_changes_with_content(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    first = build_artifact_package(scenario, kind="scenario", source_ref=_source())

    (scenario / "webui.json").write_text('{"ui": {"favorites": true}}\n', encoding="utf-8")
    second = build_artifact_package(scenario, kind="scenario", source_ref=_source())

    assert first.ref.digest != second.ref.digest
    assert first.ref.manifest_digest != second.ref.manifest_digest


def test_package_persists_builder_target_and_packaged_schema_identity(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    schema = scenario / "recipes.schema.json"
    schema.write_text('{"type":"object"}\n', encoding="utf-8")

    built = build_artifact_package(scenario, kind="scenario", source_ref=_source())
    verified = verify_artifact_package(built.archive_bytes)

    assert built.ref.builder_id == "adaos.package_builder.v1"
    assert built.ref.build_policy_digest.startswith("sha256:")
    assert built.ref.materialization_path == "scenarios/recipes"
    assert [item.lock_id for item in built.ref.schema_locks] == [
        "scenario:recipes:recipes.schema.json"
    ]
    assert verified.ref == built.ref
    assert verified.package_manifest["schema_locks"] == [
        built.ref.schema_locks[0].to_dict()
    ]


def test_package_accepts_zero_byte_files(tmp_path: Path) -> None:
    artifact = tmp_path / "empty-file-skill"
    artifact.mkdir()
    (artifact / "skill.yaml").write_text(
        "name: empty_file_skill\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (artifact / "config.json").write_bytes(b"")

    built = build_artifact_package(artifact, kind="skill", source_ref=_source())
    verified = verify_artifact_package(
        built.archive_bytes,
        expected_digest=built.ref.digest,
    )

    record = next(
        item
        for item in verified.package_manifest["files"]
        if item["path"] == "config.json"
    )
    assert record["size"] == 0


def test_package_verifier_rejects_traversal_and_symlink_entries() -> None:
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape.txt", b"no")
    with pytest.raises(PackageVerificationError, match="unsafe package path"):
        verify_artifact_package(traversal.getvalue())

    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    with pytest.raises(PackageVerificationError, match="symbolic link"):
        verify_artifact_package(symlink.getvalue())


def _package_archive_with_files(files: list[tuple[str, bytes]]) -> bytes:
    manifest = {
        "schema": "adaos.artifact.component_package.v1",
        "kind": "scenario",
        "artifact_id": "recipes",
        "version": "1.2.3",
        "source_ref": _source().to_dict(),
        "files": [
            {"path": name, "size": len(data), "digest": sha256_digest(data)}
            for name, data in files
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
        archive.writestr(".adaos/package-manifest.json", canonical_json_bytes(manifest))
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "assets/file:stream",
        "assets/CON",
        "assets/name.",
        "assets/name ",
    ],
)
def test_package_verifier_rejects_nonportable_paths(name: str) -> None:
    archive = _package_archive_with_files(
        [
            ("scenario.yaml", b"id: recipes\nversion: 1.2.3\n"),
            (name, b"unsafe"),
        ]
    )
    with pytest.raises(PackageVerificationError, match="package path"):
        verify_artifact_package(archive)


def test_package_verifier_rejects_casefold_collisions() -> None:
    archive = _package_archive_with_files(
        [
            ("scenario.yaml", b"id: recipes\nversion: 1.2.3\n"),
            ("assets/Icon.svg", b"one"),
            ("assets/icon.svg", b"two"),
        ]
    )
    with pytest.raises(PackageVerificationError, match="collide"):
        verify_artifact_package(archive)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        (".env", "TOKEN=secret\n"),
        ("assets/private.pem", "not-even-a-key\n"),
        ("config.txt", "-----BEGIN PRIVATE KEY-----\nsecret\n"),
    ],
)
def test_package_builder_rejects_secret_like_inputs(
    tmp_path: Path,
    name: str,
    content: str,
) -> None:
    scenario = _scenario(tmp_path)
    target = scenario / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    with pytest.raises(PackageBuildError, match="prohibited|private-key"):
        build_artifact_package(scenario, kind="scenario", source_ref=_source())


def test_package_verifier_rejects_external_secret_like_inputs() -> None:
    archive = _package_archive_with_files(
        [
            ("scenario.yaml", b"id: recipes\nversion: 1.2.3\n"),
            (".env", b"TOKEN=secret\n"),
        ]
    )
    with pytest.raises(PackageVerificationError, match="prohibited"):
        verify_artifact_package(archive)


def test_content_addressed_store_verifies_and_materializes_atomically(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path / "source")
    built = build_artifact_package(scenario, kind="scenario", source_ref=_source())
    store = ContentAddressedPackageStore(tmp_path / "packages")

    stored = store.put(built.archive_bytes, expected_digest=built.ref.digest)
    assert stored.ref == built.ref
    assert store.has(built.ref.digest)

    target = tmp_path / "workspace" / "scenarios" / "recipes"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old\n", encoding="utf-8")
    materialized = store.materialize(built.ref.digest, target)

    assert materialized.ref == built.ref
    assert not (target / "old.txt").exists()
    assert (target / "scenario.yaml").exists()
    assert not (target / ".adaos" / "package-manifest.json").exists()
    assert not list(target.parent.glob(".recipes.backup-*"))
    assert not list(target.parent.glob(".recipes.stage-*"))


def test_store_quarantines_corrupt_existing_package(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path / "source")
    built = build_artifact_package(scenario, kind="scenario", source_ref=_source())
    store = ContentAddressedPackageStore(tmp_path / "packages")
    store.put(built.archive_bytes)
    package_path = store.package_path(built.ref.digest)
    package_path.write_bytes(b"corrupt")

    with pytest.raises(PackageVerificationError):
        store.read(built.ref.digest)

    assert not package_path.exists()
    assert list((tmp_path / "packages" / "quarantine").glob("*.verification-failed.*.zip"))


def test_store_verify_and_extract_each_use_one_verification_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario(tmp_path / "source")
    built = build_artifact_package(scenario, kind="scenario", source_ref=_source())
    store = ContentAddressedPackageStore(tmp_path / "packages")
    store.put(built.archive_bytes)

    import adaos.services.artifact_pipeline.packages as package_module

    original = package_module.verify_artifact_package
    calls: list[str | None] = []

    def counted(data, *, expected_digest=None, limits=None):
        calls.append(expected_digest)
        return original(data, expected_digest=expected_digest, limits=limits)

    monkeypatch.setattr(package_module, "verify_artifact_package", counted)
    store.verify(built.ref.digest)
    assert calls == [built.ref.digest]

    calls.clear()
    store.extract_to_directory(built.ref.digest, tmp_path / "extracted")
    assert calls == [built.ref.digest]
