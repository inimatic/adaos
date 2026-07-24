from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from uuid import uuid4

import yaml

from adaos.domain.artifact_release import (
    ArtifactKind,
    ArtifactPackageRef,
    ArtifactReleaseContractError,
    ArtifactSourceRef,
    canonical_json_bytes,
    sha256_digest,
)
from adaos.services.artifact_pipeline.storage import replace_with_retry


PACKAGE_MANIFEST_PATH = ".adaos/package-manifest.json"
PACKAGE_MANIFEST_SCHEMA = "adaos.artifact.component_package.v1"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_EXCLUDED_FILES = {
    ".DS_Store",
    "builder.draft.json",
}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
_MANIFEST_BY_KIND: dict[ArtifactKind, str] = {
    "skill": "skill.yaml",
    "scenario": "scenario.yaml",
}


class PackageBuildError(RuntimeError):
    pass


class PackageVerificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PackageLimits:
    max_files: int = 10_000
    max_uncompressed_bytes: int = 128 * 1024 * 1024
    max_archive_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_files < 1:
            raise ValueError("max_files must be positive")
        if self.max_uncompressed_bytes < 1:
            raise ValueError("max_uncompressed_bytes must be positive")
        if self.max_archive_bytes < 1:
            raise ValueError("max_archive_bytes must be positive")


@dataclass(frozen=True, slots=True)
class BuiltArtifactPackage:
    ref: ArtifactPackageRef
    archive_bytes: bytes
    package_manifest: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class VerifiedArtifactPackage:
    ref: ArtifactPackageRef
    package_manifest: Mapping[str, Any]
    file_names: tuple[str, ...]
    uncompressed_bytes: int


def _normalized_member_name(value: str, *, error_type: type[RuntimeError]) -> str:
    raw = str(value or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise error_type(f"unsafe package path: {value!r}")
    if path.parts and ":" in path.parts[0]:
        raise error_type(f"unsafe package path: {value!r}")
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        raise error_type(f"unsafe package path: {value!r}")
    return normalized


def _excluded(relative: PurePosixPath) -> bool:
    if any(part in _EXCLUDED_DIRS for part in relative.parts):
        return True
    if relative.name in _EXCLUDED_FILES:
        return True
    return relative.suffix.lower() in _EXCLUDED_SUFFIXES


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.flag_bits |= 0x800
    return info


def _load_canonical_manifest(root: Path, kind: ArtifactKind) -> tuple[str, str, Path]:
    manifest_path = root / _MANIFEST_BY_KIND[kind]
    if not manifest_path.is_file():
        raise PackageBuildError(f"required {_MANIFEST_BY_KIND[kind]} is missing at {root}")
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise PackageBuildError(f"cannot parse {manifest_path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PackageBuildError(f"{manifest_path.name} must contain an object")
    artifact_id = str(
        payload.get("name") if kind == "skill" else payload.get("id")
    ).strip()
    version = str(payload.get("version") or "").strip()
    if not artifact_id:
        raise PackageBuildError(f"{manifest_path.name} must declare canonical id")
    if not version:
        raise PackageBuildError(f"{manifest_path.name} must declare version")
    return artifact_id, version, manifest_path


def _collect_package_files(root: Path, limits: PackageLimits) -> list[tuple[str, bytes]]:
    collected: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if _excluded(relative):
            continue
        if path.is_symlink():
            raise PackageBuildError(f"symbolic links are not allowed in packages: {relative.as_posix()}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackageBuildError(f"unsupported package input: {relative.as_posix()}")
        name = _normalized_member_name(relative.as_posix(), error_type=PackageBuildError)
        data = path.read_bytes()
        total += len(data)
        if len(collected) + 1 > limits.max_files:
            raise PackageBuildError(f"package exceeds file limit {limits.max_files}")
        if total > limits.max_uncompressed_bytes:
            raise PackageBuildError(
                f"package exceeds uncompressed size limit {limits.max_uncompressed_bytes}"
            )
        collected.append((name, data))
    return collected


def build_artifact_package(
    artifact_dir: Path,
    *,
    kind: ArtifactKind,
    source_ref: ArtifactSourceRef,
    limits: PackageLimits | None = None,
) -> BuiltArtifactPackage:
    limits = limits or PackageLimits()
    root = Path(artifact_dir).expanduser().resolve()
    if not root.is_dir():
        raise PackageBuildError(f"artifact directory does not exist: {root}")
    if kind not in _MANIFEST_BY_KIND:
        raise PackageBuildError("kind must be skill or scenario")

    artifact_id, version, _ = _load_canonical_manifest(root, kind)
    files = _collect_package_files(root, limits)
    if _MANIFEST_BY_KIND[kind] not in {name for name, _ in files}:
        raise PackageBuildError(f"required {_MANIFEST_BY_KIND[kind]} was excluded from package")

    file_records = [
        {"path": name, "size": len(data), "digest": sha256_digest(data)}
        for name, data in files
    ]
    package_manifest: dict[str, Any] = {
        "schema": PACKAGE_MANIFEST_SCHEMA,
        "kind": kind,
        "artifact_id": artifact_id,
        "version": version,
        "source_ref": source_ref.to_dict(),
        "files": file_records,
    }
    manifest_bytes = canonical_json_bytes(package_manifest)
    manifest_digest = sha256_digest(manifest_bytes)

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name, data in files:
            archive.writestr(_zip_info(name), data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        archive.writestr(
            _zip_info(PACKAGE_MANIFEST_PATH),
            manifest_bytes,
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )
    archive_bytes = buffer.getvalue()
    if len(archive_bytes) > limits.max_archive_bytes:
        raise PackageBuildError(f"package exceeds archive size limit {limits.max_archive_bytes}")
    package_digest = sha256_digest(archive_bytes)
    try:
        ref = ArtifactPackageRef(
            kind=kind,
            artifact_id=artifact_id,
            version=version,
            digest=package_digest,
            manifest_digest=manifest_digest,
            source_ref=source_ref,
        )
    except ArtifactReleaseContractError as exc:
        raise PackageBuildError(str(exc)) from exc
    return BuiltArtifactPackage(ref=ref, archive_bytes=archive_bytes, package_manifest=package_manifest)


def _entry_is_symlink(entry: zipfile.ZipInfo) -> bool:
    mode = (entry.external_attr >> 16) & 0o177777
    return stat.S_ISLNK(mode)


def _read_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, Any], bytes]:
    try:
        raw = archive.read(PACKAGE_MANIFEST_PATH)
    except KeyError as exc:
        raise PackageVerificationError(f"package is missing {PACKAGE_MANIFEST_PATH}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PackageVerificationError("package manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PackageVerificationError("package manifest must be an object")
    if value.get("schema") != PACKAGE_MANIFEST_SCHEMA:
        raise PackageVerificationError("unsupported package manifest schema")
    return value, raw


def verify_artifact_package(
    data: bytes,
    *,
    expected_digest: str | None = None,
    limits: PackageLimits | None = None,
) -> VerifiedArtifactPackage:
    limits = limits or PackageLimits()
    if len(data) > limits.max_archive_bytes:
        raise PackageVerificationError(f"package exceeds archive size limit {limits.max_archive_bytes}")
    actual_digest = sha256_digest(data)
    if expected_digest and actual_digest != str(expected_digest).strip().lower():
        raise PackageVerificationError(
            f"package digest mismatch: expected {expected_digest}, got {actual_digest}"
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(data), mode="r")
    except Exception as exc:
        raise PackageVerificationError("package is not a readable ZIP archive") from exc
    with archive:
        entries = archive.infolist()
        names = [_normalized_member_name(item.filename, error_type=PackageVerificationError) for item in entries]
        if len(names) != len(set(names)):
            raise PackageVerificationError("package contains duplicate paths")
        if len(entries) > limits.max_files + 1:
            raise PackageVerificationError(f"package exceeds file limit {limits.max_files}")
        total = 0
        for entry in entries:
            if entry.flag_bits & 0x1:
                raise PackageVerificationError(f"encrypted package entry is not allowed: {entry.filename}")
            if _entry_is_symlink(entry):
                raise PackageVerificationError(f"symbolic link entry is not allowed: {entry.filename}")
            if entry.is_dir():
                raise PackageVerificationError(f"directory entries are not allowed: {entry.filename}")
            total += int(entry.file_size)
            if total > limits.max_uncompressed_bytes:
                raise PackageVerificationError(
                    f"package exceeds uncompressed size limit {limits.max_uncompressed_bytes}"
                )

        package_manifest, manifest_bytes = _read_manifest(archive)
        raw_files = package_manifest.get("files")
        if not isinstance(raw_files, list):
            raise PackageVerificationError("package manifest files must be a list")
        expected_files: dict[str, Mapping[str, Any]] = {}
        for item in raw_files:
            if not isinstance(item, Mapping):
                raise PackageVerificationError("package manifest file record must be an object")
            name = _normalized_member_name(str(item.get("path") or ""), error_type=PackageVerificationError)
            if name == PACKAGE_MANIFEST_PATH or name in expected_files:
                raise PackageVerificationError(f"duplicate or reserved manifest file path: {name}")
            expected_files[name] = item
        archive_files = set(names) - {PACKAGE_MANIFEST_PATH}
        if archive_files != set(expected_files):
            missing = sorted(set(expected_files) - archive_files)
            extra = sorted(archive_files - set(expected_files))
            raise PackageVerificationError(f"package file set mismatch: missing={missing} extra={extra}")
        for name, record in expected_files.items():
            raw = archive.read(name)
            if int(record.get("size") or -1) != len(raw):
                raise PackageVerificationError(f"package file size mismatch: {name}")
            expected_file_digest = str(record.get("digest") or "").strip().lower()
            if sha256_digest(raw) != expected_file_digest:
                raise PackageVerificationError(f"package file digest mismatch: {name}")

        source = package_manifest.get("source_ref")
        if not isinstance(source, Mapping):
            raise PackageVerificationError("package manifest source_ref must be an object")
        try:
            ref = ArtifactPackageRef(
                kind=package_manifest.get("kind"),
                artifact_id=package_manifest.get("artifact_id"),
                version=package_manifest.get("version"),
                digest=actual_digest,
                manifest_digest=sha256_digest(manifest_bytes),
                source_ref=ArtifactSourceRef.from_mapping(source),
            )
        except ArtifactReleaseContractError as exc:
            raise PackageVerificationError(str(exc)) from exc
        return VerifiedArtifactPackage(
            ref=ref,
            package_manifest=package_manifest,
            file_names=tuple(sorted(expected_files)),
            uncompressed_bytes=sum(int(item.get("size") or 0) for item in expected_files.values()),
        )


class ContentAddressedPackageStore:
    def __init__(self, root: Path, *, limits: PackageLimits | None = None):
        self.root = Path(root).expanduser().resolve()
        self.limits = limits or PackageLimits()

    @staticmethod
    def _hex_digest(digest: str) -> str:
        value = str(digest or "").strip().lower()
        if not value.startswith("sha256:") or len(value) != 71:
            raise PackageVerificationError("package digest must be sha256:<64 lowercase hex characters>")
        token = value.split(":", 1)[1]
        if any(char not in "0123456789abcdef" for char in token):
            raise PackageVerificationError("package digest must be sha256:<64 lowercase hex characters>")
        return token

    def package_path(self, digest: str) -> Path:
        token = self._hex_digest(digest)
        return self.root / "sha256" / token[:2] / f"{token}.zip"

    def has(self, digest: str) -> bool:
        return self.package_path(digest).is_file()

    def put(self, data: bytes, *, expected_digest: str | None = None) -> VerifiedArtifactPackage:
        verified = verify_artifact_package(data, expected_digest=expected_digest, limits=self.limits)
        target = self.package_path(verified.ref.digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                existing = target.read_bytes()
                return verify_artifact_package(
                    existing,
                    expected_digest=verified.ref.digest,
                    limits=self.limits,
                )
            except Exception:
                self._quarantine_path(target, reason="corrupt-existing")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            replace_with_retry(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return verified

    def read(self, digest: str) -> bytes:
        path = self.package_path(digest)
        if not path.is_file():
            raise FileNotFoundError(f"package not found: {digest}")
        data = path.read_bytes()
        try:
            verify_artifact_package(data, expected_digest=digest, limits=self.limits)
        except Exception:
            self._quarantine_path(path, reason="verification-failed")
            raise
        return data

    def verify(self, digest: str) -> VerifiedArtifactPackage:
        return verify_artifact_package(self.read(digest), expected_digest=digest, limits=self.limits)

    def materialize(self, digest: str, target: Path) -> VerifiedArtifactPackage:
        target = Path(target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.parent / f".{target.name}.stage-{uuid4().hex}"
        backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
        try:
            verified = self.extract_to_directory(digest, staged)
            target_moved = False
            try:
                if target.exists():
                    target.replace(backup)
                    target_moved = True
                staged.replace(target)
            except Exception:
                if target_moved and backup.exists() and not target.exists():
                    backup.replace(target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
        return verified

    def extract_to_directory(self, digest: str, target: Path) -> VerifiedArtifactPackage:
        """Verify and extract a package into a new directory without switching it live."""

        data = self.read(digest)
        verified = verify_artifact_package(data, expected_digest=digest, limits=self.limits)
        target = Path(target).expanduser().resolve()
        if target.exists():
            raise FileExistsError(f"package extraction target already exists: {target}")
        target.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(io.BytesIO(data), mode="r") as archive:
                for name in verified.file_names:
                    destination = (target / Path(name)).resolve()
                    if target != destination and target not in destination.parents:
                        raise PackageVerificationError(f"package entry escapes materialization root: {name}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(name))
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return verified

    def quarantine(self, digest: str, *, reason: str) -> Path:
        path = self.package_path(digest)
        if not path.exists():
            raise FileNotFoundError(f"package not found: {digest}")
        return self._quarantine_path(path, reason=reason)

    def _quarantine_path(self, path: Path, *, reason: str) -> Path:
        safe_reason = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in reason).strip("-")
        quarantine_root = self.root / "quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        target = quarantine_root / f"{path.stem}.{safe_reason or 'quarantine'}.{uuid4().hex}.zip"
        path.replace(target)
        return target


__all__ = [
    "BuiltArtifactPackage",
    "ContentAddressedPackageStore",
    "PACKAGE_MANIFEST_PATH",
    "PACKAGE_MANIFEST_SCHEMA",
    "PackageBuildError",
    "PackageLimits",
    "PackageVerificationError",
    "VerifiedArtifactPackage",
    "build_artifact_package",
    "verify_artifact_package",
]
