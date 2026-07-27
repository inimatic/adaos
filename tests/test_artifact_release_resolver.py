from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    DependencyRequirement,
    DependencyResolutionError,
    PackageCatalog,
    build_project_release,
    parse_artifact_requirements,
)


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
    )


def _package(kind: str, artifact_id: str, version: str, token: str) -> ArtifactPackageRef:
    return ArtifactPackageRef(
        kind=kind,
        artifact_id=artifact_id,
        version=version,
        digest="sha256:" + token * 64,
        manifest_digest="sha256:" + "f" * 64,
        source_ref=_source(),
    )


def test_parse_requirements_accepts_legacy_and_structured_skill_entries(tmp_path: Path) -> None:
    manifest = yaml.safe_load(
        """
depends:
  - legacy_skill
runtime:
  skills:
    required:
      - id: shopping_list
        version: ^1.4.0
    optional:
      - id: telemetry
        version: '~2.1.0'
"""
    )

    requirements = parse_artifact_requirements(manifest, kind="scenario")

    assert [item.key for item in requirements] == [
        "skill:legacy_skill",
        "skill:shopping_list",
        "skill:telemetry",
    ]
    assert requirements[1].version_spec == ">=1.4.0,<2.0.0"
    assert requirements[2].version_spec == ">=2.1.0,<2.2.0"
    assert requirements[2].optional is True


def test_release_resolves_exact_dependency_and_reverse_consumers() -> None:
    scenario = _package("scenario", "recipes", "1.2.3", "a")
    shopping_old = _package("skill", "shopping_list", "1.5.0", "b")
    shopping_new = _package("skill", "shopping_list", "1.8.0", "c")
    shopping_unsupported = _package("skill", "shopping_list", "2.0.0", "d")
    requirements = {
        scenario.digest: (
            DependencyRequirement("skill", "shopping_list", "^1.4.0"),
        )
    }

    plan = build_project_release(
        project_id="recipes",
        version="1.2.3",
        source_ref=_source(),
        components=(scenario,),
        catalog=PackageCatalog((shopping_old, shopping_new, shopping_unsupported)),
        requirements_by_package=requirements,
    )

    dependency = plan.release.resolved_dependencies[0]
    assert dependency.version == "1.8.0"
    assert dependency.package_digest == shopping_new.digest
    assert plan.bindings[0].dependency == "skill:shopping_list"
    assert plan.reverse_consumers == {"skill:shopping_list": ("scenario:recipes",)}
    assert plan.release.release_digest == plan.release.computed_digest()


def test_release_rejects_missing_ambiguous_incompatible_and_cyclic_dependencies() -> None:
    scenario = _package("scenario", "recipes", "1.0.0", "a")
    skill_a = _package("skill", "a", "1.0.0", "b")
    skill_a_other = _package("skill", "a", "1.0.0", "c")
    skill_b = _package("skill", "b", "1.0.0", "d")

    with pytest.raises(DependencyResolutionError, match="no compatible package"):
        build_project_release(
            project_id="recipes",
            version="1.0.0",
            source_ref=_source(),
            components=(scenario,),
            catalog=PackageCatalog(),
            requirements_by_package={
                scenario.digest: (DependencyRequirement("skill", "missing", "^1.0.0"),)
            },
        )

    with pytest.raises(DependencyResolutionError, match="ambiguous package"):
        build_project_release(
            project_id="recipes",
            version="1.0.0",
            source_ref=_source(),
            components=(scenario,),
            catalog=PackageCatalog((skill_a, skill_a_other)),
            requirements_by_package={
                scenario.digest: (DependencyRequirement("skill", "a", "1.0.0"),)
            },
        )

    with pytest.raises(DependencyResolutionError, match="no compatible package"):
        build_project_release(
            project_id="recipes",
            version="1.0.0",
            source_ref=_source(),
            components=(scenario,),
            catalog=PackageCatalog((skill_a,)),
            requirements_by_package={
                scenario.digest: (DependencyRequirement("skill", "a", ">=2.0.0"),)
            },
        )

    with pytest.raises(DependencyResolutionError, match="cycle"):
        build_project_release(
            project_id="recipes",
            version="1.0.0",
            source_ref=_source(),
            components=(scenario,),
            catalog=PackageCatalog((skill_a, skill_b)),
            requirements_by_package={
                scenario.digest: (DependencyRequirement("skill", "a", "1.0.0"),),
                skill_a.digest: (DependencyRequirement("skill", "b", "1.0.0"),),
                skill_b.digest: (DependencyRequirement("skill", "a", "1.0.0"),),
            },
        )


def test_optional_missing_dependency_does_not_enter_release() -> None:
    scenario = _package("scenario", "recipes", "1.0.0", "a")
    plan = build_project_release(
        project_id="recipes",
        version="1.0.0",
        source_ref=_source(),
        components=(scenario,),
        catalog=PackageCatalog(),
        requirements_by_package={
            scenario.digest: (
                DependencyRequirement("skill", "telemetry", "^1.0.0", optional=True),
            )
        },
    )

    assert plan.release.resolved_dependencies == ()
    assert plan.bindings == ()


def test_release_rebuilds_all_bindings_after_complete_constraint_selection() -> None:
    broad_consumer = _package("scenario", "broad", "1.0.0", "a")
    narrow_consumer = _package("scenario", "narrow", "1.0.0", "b")
    shared_v1 = _package("skill", "shared", "1.0.0", "c")
    shared_v2 = _package("skill", "shared", "2.0.0", "d")

    plan = build_project_release(
        project_id="combined",
        version="1.0.0",
        source_ref=_source(),
        components=(broad_consumer, narrow_consumer),
        catalog=PackageCatalog((shared_v1, shared_v2)),
        requirements_by_package={
            broad_consumer.digest: (
                DependencyRequirement("skill", "shared", ">=1.0.0,<3.0.0"),
            ),
            narrow_consumer.digest: (
                DependencyRequirement("skill", "shared", ">=1.0.0,<2.0.0"),
            ),
        },
    )

    assert plan.release.resolved_dependencies[0].package_digest == shared_v1.digest
    assert {binding.package_digest for binding in plan.bindings} == {shared_v1.digest}
    assert plan.reverse_consumers == {
        "skill:shared": ("scenario:broad", "scenario:narrow")
    }


def test_stored_release_plan_rejects_binding_outside_final_selection() -> None:
    scenario = _package("scenario", "recipes", "1.0.0", "a")
    shared = _package("skill", "shared", "1.0.0", "b")
    plan = build_project_release(
        project_id="recipes",
        version="1.0.0",
        source_ref=_source(),
        components=(scenario,),
        catalog=PackageCatalog((shared,)),
        requirements_by_package={
            scenario.digest: (DependencyRequirement("skill", "shared", "1.0.0"),)
        },
    )
    payload = {"schema": "adaos.artifact.release_plan.v1", **plan.explain()}
    payload["bindings"][0]["package_digest"] = "sha256:" + "e" * 64

    from adaos.services.artifact_pipeline import ReleasePlan

    with pytest.raises(DependencyResolutionError, match="inconsistent dependency"):
        ReleasePlan.from_mapping(payload)
