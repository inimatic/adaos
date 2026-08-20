from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.sdk.developer.compositions import normalized_definition

from .channels import ReleaseRepository
from .packages import BuiltArtifactPackage, ContentAddressedPackageStore, build_artifact_package
from .releases import (
    DependencyRequirement,
    PackageCatalog,
    ReleasePlan,
    build_project_release,
    parse_artifact_requirements,
)


class ProjectReleaseBuildError(RuntimeError):
    pass


_COMPONENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ProjectReleaseBuildResult:
    plan: ReleasePlan
    release_path: Path
    package_paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        release = self.plan.release
        return {
            "ok": True,
            "schema": "adaos.artifact.project_release_build.v1",
            "project_id": release.project_id,
            "version": release.version,
            "release_digest": release.release_digest or release.computed_digest(),
            "release_path": str(self.release_path),
            "packages": [item.to_dict() for item in self.plan.packages],
            "package_paths": [str(item) for item in self.package_paths],
            "composition_lock": release.composition_lock.to_dict()
            if release.composition_lock is not None
            else None,
        }


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProjectReleaseBuildError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ProjectReleaseBuildError(f"manifest must contain an object: {path}")
    return dict(value)


def _component_ref(value: str) -> tuple[str, str]:
    kind, separator, artifact_id = str(value or "").strip().partition(":")
    if (
        separator != ":"
        or kind not in {"skill", "scenario"}
        or not _COMPONENT_ID_RE.fullmatch(artifact_id)
    ):
        raise ProjectReleaseBuildError(f"unsupported component ref: {value!r}")
    return kind, artifact_id


def _component_root(workspace_root: Path, ref: str) -> Path:
    kind, artifact_id = _component_ref(ref)
    root = (
        workspace_root
        / ("skills" if kind == "skill" else "scenarios")
        / artifact_id
    ).resolve()
    if workspace_root not in root.parents or not root.is_dir():
        raise ProjectReleaseBuildError(f"component source is unavailable: {ref}")
    return root


def _source_ref(base: ArtifactSourceRef, scope: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge=base.forge,
        repository=base.repository,
        revision=base.revision,
        path_scope=(scope,),
    )


def build_workspace_project_release(
    *,
    project_dir: Path,
    workspace_root: Path,
    source_ref: ArtifactSourceRef,
    package_store: ContentAddressedPackageStore,
    release_repository: ReleaseRepository,
    validation_evidence: tuple[Mapping[str, Any], ...] = (),
) -> ProjectReleaseBuildResult:
    workspace = Path(workspace_root).expanduser().resolve()
    project_root = Path(project_dir).expanduser().resolve()
    if workspace not in project_root.parents or not project_root.is_dir():
        raise ProjectReleaseBuildError(
            "Project source must be inside the selected Workspace"
        )
    definition = normalized_definition(_read_yaml(project_root / "project.yaml"))
    project_id = str(definition["id"])

    project_dependencies = [
        item
        for item in definition["components"]["dependencies"]
        if str(item.get("ref") or "").startswith("project:")
    ]
    if project_dependencies:
        raise ProjectReleaseBuildError(
            "Project dependencies require exact ProjectDependencyLock inputs"
        )

    owned_refs = tuple(
        str(item["ref"]) for item in definition["components"]["owned"]
    )
    pending: list[tuple[str, bool]] = [(item, False) for item in owned_refs]
    for item in definition["components"]["dependencies"]:
        ref = str(item.get("ref") or "")
        if ref.startswith(("skill:", "scenario:")):
            pending.append((ref, False))

    built_by_ref: dict[str, BuiltArtifactPackage] = {}
    requirements_by_digest: dict[str, tuple[DependencyRequirement, ...]] = {}
    while pending:
        ref, optional = pending.pop(0)
        if ref in built_by_ref:
            continue
        try:
            root = _component_root(workspace, ref)
        except ProjectReleaseBuildError:
            if optional:
                continue
            raise
        kind, artifact_id = _component_ref(ref)
        relative = root.relative_to(workspace).as_posix() + "/"
        built = build_artifact_package(
            root,
            kind=kind,  # type: ignore[arg-type]
            source_ref=_source_ref(source_ref, relative),
        )
        if built.ref.artifact_id != artifact_id:
            raise ProjectReleaseBuildError(
                f"component identity differs from Project ref: {ref}"
            )
        built_by_ref[ref] = built
        manifest_name = "skill.yaml" if kind == "skill" else "scenario.yaml"
        requirements = parse_artifact_requirements(
            _read_yaml(root / manifest_name), kind=kind  # type: ignore[arg-type]
        )
        requirements_by_digest[built.ref.digest] = requirements
        pending.extend((item.key, item.optional) for item in requirements)

    primary_ref = next(
        str(item["ref"])
        for item in definition["components"]["owned"]
        if item.get("role") == "primary"
    )
    primary = built_by_ref[primary_ref]
    declared_requirements = list(
        requirements_by_digest.get(primary.ref.digest, ())
    )
    for item in definition["components"]["dependencies"]:
        ref = str(item.get("ref") or "")
        if not ref.startswith(("skill:", "scenario:")):
            continue
        kind, artifact_id = _component_ref(ref)
        declared_requirements.append(
            DependencyRequirement(
                kind=kind,  # type: ignore[arg-type]
                artifact_id=artifact_id,
                version_spec=str(item.get("version") or ""),
            )
        )
    requirements_by_digest[primary.ref.digest] = tuple(declared_requirements)

    catalog = PackageCatalog(item.ref for item in built_by_ref.values())
    owned = tuple(built_by_ref[ref].ref for ref in owned_refs)
    project_scope = project_root.relative_to(workspace).as_posix() + "/"
    plan = build_project_release(
        project_id=project_id,
        version=str(definition["version"]),
        source_ref=_source_ref(source_ref, project_scope),
        components=owned,
        catalog=catalog,
        requirements_by_package=requirements_by_digest,
        validation_evidence=validation_evidence,
        project_definition=definition,
    )
    selected = {item.digest for item in plan.packages}
    package_paths: list[Path] = []
    for built in sorted(built_by_ref.values(), key=lambda item: item.ref.key):
        if built.ref.digest not in selected:
            continue
        package_store.put(built.archive_bytes, expected_digest=built.ref.digest)
        package_paths.append(package_store.package_path(built.ref.digest))
    release_path = release_repository.put_release(plan)
    return ProjectReleaseBuildResult(
        plan=plan,
        release_path=release_path,
        package_paths=tuple(package_paths),
    )


__all__ = [
    "ProjectReleaseBuildError",
    "ProjectReleaseBuildResult",
    "build_workspace_project_release",
]
