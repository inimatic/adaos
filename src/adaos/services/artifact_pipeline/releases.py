from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from adaos.domain.artifact_release import (
    ArtifactKind,
    ArtifactPackageRef,
    ArtifactSourceRef,
    DependencyBinding,
    ProjectRelease,
    ResolvedDependency,
)


class DependencyResolutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DependencyRequirement:
    kind: ArtifactKind
    artifact_id: str
    version_spec: str = ""
    optional: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"scenario", "skill"}:
            raise DependencyResolutionError("dependency kind must be scenario or skill")
        artifact_id = str(self.artifact_id or "").strip()
        if not artifact_id:
            raise DependencyResolutionError("dependency id must not be empty")
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "version_spec", normalize_version_spec(self.version_spec))

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.artifact_id}"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "id": self.artifact_id,
            "optional": self.optional,
        }
        if self.version_spec:
            payload["version"] = self.version_spec
        return payload


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    release: ProjectRelease
    packages: tuple[ArtifactPackageRef, ...]
    bindings: tuple[DependencyBinding, ...]
    reverse_consumers: Mapping[str, tuple[str, ...]]

    def explain(self) -> dict[str, Any]:
        return {
            "release": self.release.to_dict(),
            "packages": [item.to_dict() for item in self.packages],
            "bindings": [item.to_dict() for item in self.bindings],
            "reverse_consumers": {
                key: list(value) for key, value in sorted(self.reverse_consumers.items())
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleasePlan":
        allowed = {"schema", "release", "packages", "bindings", "reverse_consumers"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise DependencyResolutionError(
                f"release plan contains unsupported fields: {', '.join(unknown)}"
            )
        if value.get("schema") != "adaos.artifact.release_plan.v1":
            raise DependencyResolutionError("unsupported release plan schema")
        missing = sorted(allowed - set(value))
        if missing:
            raise DependencyResolutionError(
                f"release plan is missing required fields: {', '.join(missing)}"
            )
        raw_release = value.get("release")
        raw_packages = value.get("packages")
        raw_bindings = value.get("bindings")
        raw_reverse = value.get("reverse_consumers")
        if not isinstance(raw_release, Mapping):
            raise DependencyResolutionError("release plan release must be an object")
        if not isinstance(raw_packages, list) or not isinstance(raw_bindings, list):
            raise DependencyResolutionError("release plan packages and bindings must be lists")
        if not isinstance(raw_reverse, Mapping):
            raise DependencyResolutionError("release plan reverse_consumers must be an object")
        if any(not isinstance(item, Mapping) for item in raw_packages):
            raise DependencyResolutionError("release plan packages contain a malformed member")
        if any(not isinstance(item, Mapping) for item in raw_bindings):
            raise DependencyResolutionError("release plan bindings contain a malformed member")
        release = ProjectRelease.from_mapping(raw_release)
        packages = tuple(
            ArtifactPackageRef.from_mapping(item)
            for item in raw_packages
        )
        bindings = tuple(
            DependencyBinding.from_mapping(item)
            for item in raw_bindings
        )
        reverse: dict[str, set[str]] = defaultdict(set)
        for binding in bindings:
            reverse[binding.dependency].add(binding.consumer)
        plan = cls(
            release=release,
            packages=packages,
            bindings=bindings,
            reverse_consumers={
                key: tuple(sorted(consumers)) for key, consumers in sorted(reverse.items())
            },
        )
        package_by_key = {item.key: item for item in packages}
        if len(package_by_key) != len(packages):
            raise DependencyResolutionError("stored release plan has duplicate package identities")
        materialization_targets = {
            item.materialization_path
            or (f"skills/{item.artifact_id}" if item.kind == "skill" else f"scenarios/{item.artifact_id}")
            for item in packages
        }
        if len(materialization_targets) != len(packages):
            raise DependencyResolutionError(
                "stored release plan has duplicate materialization targets"
            )
        if release.contract_locks_present:
            expected_schema_locks = tuple(
                sorted(
                    (
                        lock
                        for package in packages
                        for lock in package.schema_locks
                    ),
                    key=lambda item: item.lock_id,
                )
            )
            if release.schema_locks != expected_schema_locks:
                raise DependencyResolutionError(
                    "stored release schema_locks do not match selected packages"
                )
        for component in release.components:
            if package_by_key.get(component.key) != component:
                raise DependencyResolutionError(
                    f"stored release plan is missing component {component.key}"
                )
        for dependency in release.resolved_dependencies:
            package = package_by_key.get(dependency.key)
            if package is None or package.digest != dependency.package_digest:
                raise DependencyResolutionError(
                    f"stored release plan is missing dependency {dependency.key}"
                )
        for binding in bindings:
            package = package_by_key.get(binding.dependency)
            if package is None or package.digest != binding.package_digest:
                raise DependencyResolutionError(
                    f"stored release plan binding for {binding.consumer} selects an inconsistent "
                    f"dependency {binding.dependency}"
                )
            if binding.consumer not in package_by_key:
                raise DependencyResolutionError(
                    f"stored release plan binding has unknown consumer {binding.consumer}"
                )
        expected_reverse = {
            key: tuple(sorted(consumers)) for key, consumers in sorted(reverse.items())
        }
        normalized_reverse: dict[str, tuple[str, ...]] = {}
        for key, consumers in raw_reverse.items():
            if not isinstance(key, str) or not isinstance(consumers, list) or any(
                not isinstance(item, str) for item in consumers
            ):
                raise DependencyResolutionError(
                    "release plan reverse_consumers contains a malformed member"
                )
            normalized_reverse[key] = tuple(sorted(consumers))
        if normalized_reverse != expected_reverse:
            raise DependencyResolutionError(
                "release plan reverse_consumers does not match dependency bindings"
            )
        return plan


def _semver_triplet(value: str) -> tuple[int, int, int]:
    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise DependencyResolutionError(f"invalid semantic version: {value!r}") from exc
    release = tuple(parsed.release) + (0, 0, 0)
    return int(release[0]), int(release[1]), int(release[2])


def normalize_version_spec(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "*":
        return ""
    if raw.startswith("^"):
        base = raw[1:].strip()
        major, minor, patch = _semver_triplet(base)
        if major > 0:
            ceiling = f"{major + 1}.0.0"
        elif minor > 0:
            ceiling = f"0.{minor + 1}.0"
        else:
            ceiling = f"0.0.{patch + 1}"
        raw = f">={base},<{ceiling}"
    elif raw.startswith("~") and not raw.startswith("~="):
        base = raw[1:].strip()
        major, minor, _ = _semver_triplet(base)
        raw = f">={base},<{major}.{minor + 1}.0"
    elif "*" in raw and not raw.startswith(("==", "!=")):
        raw = f"=={raw}"
    elif not raw.startswith(("<", ">", "=", "!", "~")):
        raw = f"=={raw}"
    try:
        SpecifierSet(raw)
    except InvalidSpecifier as exc:
        raise DependencyResolutionError(f"invalid dependency version range: {value!r}") from exc
    return raw


def _requirement_from_value(
    value: Any,
    *,
    default_kind: ArtifactKind,
    optional: bool,
) -> DependencyRequirement | None:
    if isinstance(value, str):
        artifact_id = value.strip()
        if not artifact_id:
            return None
        return DependencyRequirement(default_kind, artifact_id, optional=optional)
    if not isinstance(value, Mapping):
        return None
    artifact_id = str(value.get("id") or value.get("name") or "").strip()
    if not artifact_id:
        return None
    kind = str(value.get("kind") or default_kind).strip().lower()
    return DependencyRequirement(
        kind=kind,  # type: ignore[arg-type]
        artifact_id=artifact_id,
        version_spec=value.get("version") or value.get("version_spec") or "",
        optional=value.get("optional") is True or optional,
    )


def parse_artifact_requirements(
    manifest: Mapping[str, Any],
    *,
    kind: ArtifactKind,
) -> tuple[DependencyRequirement, ...]:
    """Read canonical ranged dependencies while preserving legacy declarations."""

    result: list[DependencyRequirement] = []

    def append(value: Any, *, default_kind: ArtifactKind, optional: bool = False) -> None:
        requirement = _requirement_from_value(
            value,
            default_kind=default_kind,
            optional=optional,
        )
        if requirement is None:
            return
        previous = next((item for item in result if item.key == requirement.key), None)
        if previous is None:
            result.append(requirement)
            return
        if previous.version_spec != requirement.version_spec or previous.optional != requirement.optional:
            raise DependencyResolutionError(
                f"conflicting duplicate dependency declaration for {requirement.key}"
            )

    legacy = manifest.get("depends")
    if isinstance(legacy, (list, tuple)):
        for value in legacy:
            append(value, default_kind="skill")

    dependencies = manifest.get("dependencies")
    if isinstance(dependencies, (list, tuple)):
        for value in dependencies:
            append(value, default_kind="skill")

    runtime = manifest.get("runtime")
    if isinstance(runtime, Mapping):
        skills = runtime.get("skills")
        if isinstance(skills, Mapping):
            for value in skills.get("required") or ():
                append(value, default_kind="skill")
            for value in skills.get("optional") or ():
                append(value, default_kind="skill", optional=True)

    return tuple(sorted(result, key=lambda item: item.key))


class PackageCatalog:
    def __init__(self, packages: Iterable[ArtifactPackageRef] = ()) -> None:
        self._packages: dict[str, list[ArtifactPackageRef]] = defaultdict(list)
        for package in packages:
            self.add(package)

    def add(self, package: ArtifactPackageRef) -> None:
        if all(item.digest != package.digest for item in self._packages[package.key]):
            self._packages[package.key].append(package)

    def versions(self, key: str) -> tuple[ArtifactPackageRef, ...]:
        return tuple(
            sorted(
                self._packages.get(key, ()),
                key=lambda item: (Version(item.version), item.digest),
                reverse=True,
            )
        )

    @property
    def package_count(self) -> int:
        return sum(len(items) for items in self._packages.values())

    def resolve(
        self,
        key: str,
        requirements: Iterable[DependencyRequirement],
    ) -> ArtifactPackageRef | None:
        constraints = tuple(requirements)
        candidates = self.versions(key)
        matches = [
            item
            for item in candidates
            if all(
                not requirement.version_spec
                or Version(item.version) in SpecifierSet(requirement.version_spec)
                for requirement in constraints
            )
        ]
        if not matches:
            if constraints and all(item.optional for item in constraints):
                return None
            requested = ", ".join(item.version_spec or "*" for item in constraints) or "*"
            available = ", ".join(item.version for item in candidates) or "none"
            raise DependencyResolutionError(
                f"no compatible package for {key}; requested={requested}; available={available}"
            )
        highest = matches[0].version
        top = [item for item in matches if item.version == highest]
        if len({item.digest for item in top}) != 1:
            raise DependencyResolutionError(
                f"ambiguous package for {key}@{highest}: multiple content digests"
            )
        return top[0]


def _assert_acyclic(graph: Mapping[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        if key in visiting:
            start = path.index(key) if key in path else 0
            cycle = path[start:] + (key,)
            raise DependencyResolutionError(f"dependency cycle detected: {' -> '.join(cycle)}")
        if key in visited:
            return
        visiting.add(key)
        for dependency in sorted(graph.get(key, ())):
            visit(dependency, path + (key,))
        visiting.remove(key)
        visited.add(key)

    for node in sorted(graph):
        visit(node, ())


def build_project_release(
    *,
    project_id: str,
    version: str,
    source_ref: ArtifactSourceRef,
    components: Iterable[ArtifactPackageRef],
    catalog: PackageCatalog,
    requirements_by_package: Mapping[str, Iterable[DependencyRequirement]] | None = None,
    permissions: Iterable[str] = (),
    migrations: Iterable[Mapping[str, Any]] = (),
    validation_evidence: Iterable[Mapping[str, Any]] = (),
) -> ReleasePlan:
    owned = tuple(components)
    if not owned:
        raise DependencyResolutionError("project release requires at least one component")
    requirements_by_package = requirements_by_package or {}
    selected: dict[str, ArtifactPackageRef] = {item.key: item for item in owned}
    if len(selected) != len(owned):
        raise DependencyResolutionError("project components must have unique identities")

    owned_by_key = dict(selected)
    constraints: dict[str, list[DependencyRequirement]] = defaultdict(list)
    graph: dict[str, set[str]] = defaultdict(set)
    consumer_requirements: list[tuple[ArtifactPackageRef, DependencyRequirement]] = []
    seen_selections: set[tuple[tuple[str, str], ...]] = set()
    max_iterations = max(
        8,
        len(owned) + catalog.package_count + 1,
    )

    for _ in range(max_iterations):
        fingerprint = tuple(sorted((key, package.digest) for key, package in selected.items()))
        if fingerprint in seen_selections:
            raise DependencyResolutionError(
                "dependency resolution oscillated between incompatible complete selections"
            )
        seen_selections.add(fingerprint)

        next_constraints: dict[str, list[DependencyRequirement]] = defaultdict(list)
        next_graph: dict[str, set[str]] = defaultdict(set)
        next_consumer_requirements: list[
            tuple[ArtifactPackageRef, DependencyRequirement]
        ] = []
        pending: deque[ArtifactPackageRef] = deque(owned)
        expanded: set[str] = set()
        while pending:
            consumer = pending.popleft()
            consumer_token = f"{consumer.key}@{consumer.digest}"
            if consumer_token in expanded:
                continue
            expanded.add(consumer_token)
            for requirement in requirements_by_package.get(consumer.digest, ()):
                next_graph[consumer.key].add(requirement.key)
                next_constraints[requirement.key].append(requirement)
                next_consumer_requirements.append((consumer, requirement))
                dependency = selected.get(requirement.key)
                if dependency is not None:
                    pending.append(dependency)

        next_selected = dict(owned_by_key)
        for key, requirements in sorted(next_constraints.items()):
            owned_package = owned_by_key.get(key)
            if owned_package is not None:
                compatible = all(
                    not requirement.version_spec
                    or Version(owned_package.version)
                    in SpecifierSet(requirement.version_spec)
                    for requirement in requirements
                )
                if not compatible:
                    requested = ", ".join(
                        requirement.version_spec or "*" for requirement in requirements
                    )
                    raise DependencyResolutionError(
                        f"owned component {key}@{owned_package.version} conflicts with "
                        f"dependency constraints: {requested}"
                    )
                continue
            resolved = catalog.resolve(key, requirements)
            if resolved is not None:
                next_selected[key] = resolved

        next_fingerprint = tuple(
            sorted((key, package.digest) for key, package in next_selected.items())
        )
        if next_fingerprint == fingerprint:
            constraints = next_constraints
            graph = next_graph
            consumer_requirements = next_consumer_requirements
            selected = next_selected
            break
        selected = next_selected
    else:
        raise DependencyResolutionError(
            f"dependency resolution did not converge within {max_iterations} iterations"
        )

    bindings: dict[tuple[str, str], DependencyBinding] = {}
    for consumer, requirement in consumer_requirements:
        resolved = selected.get(requirement.key)
        if resolved is None:
            continue
        bindings[(consumer.key, requirement.key)] = DependencyBinding(
            consumer=consumer.key,
            dependency=requirement.key,
            package_digest=resolved.digest,
        )

    _assert_acyclic(graph)
    owned_keys = set(owned_by_key)
    resolved_dependencies = tuple(
        ResolvedDependency(
            kind=package.kind,
            artifact_id=package.artifact_id,
            version=package.version,
            package_digest=package.digest,
            version_spec=",".join(
                sorted(
                    {
                        item.version_spec
                        for item in constraints.get(key, ())
                        if item.version_spec
                    }
                )
            ),
            optional=bool(constraints.get(key))
            and all(item.optional for item in constraints[key]),
        )
        for key, package in sorted(selected.items())
        if key not in owned_keys
    )
    release = ProjectRelease(
        project_id=project_id,
        version=version,
        source_ref=source_ref,
        components=owned,
        resolved_dependencies=resolved_dependencies,
        permissions=tuple(permissions),
        migrations=tuple(migrations),
        validation_evidence=tuple(validation_evidence),
        schema_locks=tuple(
            lock
            for package in sorted(selected.values(), key=lambda item: item.key)
            for lock in package.schema_locks
        ),
    ).seal()
    reverse: dict[str, set[str]] = defaultdict(set)
    for binding in bindings.values():
        reverse[binding.dependency].add(binding.consumer)
    return ReleasePlan(
        release=release,
        packages=tuple(sorted(selected.values(), key=lambda item: item.key)),
        bindings=tuple(
            sorted(bindings.values(), key=lambda item: (item.consumer, item.dependency))
        ),
        reverse_consumers={
            key: tuple(sorted(values)) for key, values in sorted(reverse.items())
        },
    )


__all__ = [
    "DependencyRequirement",
    "DependencyResolutionError",
    "PackageCatalog",
    "ReleasePlan",
    "build_project_release",
    "normalize_version_spec",
    "parse_artifact_requirements",
]
