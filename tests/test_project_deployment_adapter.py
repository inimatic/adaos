from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, ProjectRelease
from adaos.domain.project_deployment import (
    DeploymentPlanChange,
    NodeInventoryRecord,
    ProjectDeployment,
)
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore,
    build_artifact_package,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.project_deployment import (
    LocalComponentDeploymentAdapter,
    NoopComponentLifecycleHooks,
    RoutingComponentDeploymentAdapter,
    UncertainDeploymentPhaseError,
)


_NOW = "2026-08-19T18:00:00+00:00"
_SOURCE = ArtifactSourceRef(
    forge="github",
    repository="inimatic/adaos-registry",
    revision="0123456789abcdef0123456789abcdef01234567",
    path_scope=("skills/test_worker/",),
)


def _package(tmp_path: Path):
    source = tmp_path / "source" / "test_worker"
    source.mkdir(parents=True)
    (source / "skill.yaml").write_text(
        "name: test_worker\nversion: 1.2.3\nentry: test_worker:register\n",
        encoding="utf-8",
    )
    (source / "test_worker.py").write_text(
        "def register():\n    return None\n", encoding="utf-8"
    )
    return build_artifact_package(source, kind="skill", source_ref=_SOURCE)


def _release(package) -> ReleasePlan:
    release = ProjectRelease(
        project_id="test_project",
        version="1.0.0",
        source_ref=_SOURCE,
        components=(package.ref,),
    ).seal()
    return ReleasePlan(
        release=release,
        packages=(package.ref,),
        bindings=(),
        reverse_consumers={},
    )


def _desired(release: ReleasePlan) -> ProjectDeployment:
    from adaos.domain.project_deployment import ComponentPlacementPolicy

    return ProjectDeployment(
        deployment_id="test-project-home",
        project_ref="project:test_project",
        release_digest=str(release.release.release_digest),
        subnet_id="home",
        revision=1,
        placements=(
            ComponentPlacementPolicy(
                component_ref="skill:test_worker",
                mode="selected_nodes",
                selected_node_ids=("node-a",),
            ),
        ),
        status="active",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _node(node_id: str) -> NodeInventoryRecord:
    return NodeInventoryRecord(
        node_id=node_id,
        subnet_id="home",
        trust_state="trusted",
        online=True,
        architecture="x86_64",
        runtime_version="0.1.900",
        capabilities=("project.activate",),
        protocols={"project_activation": "1"},
        labels={},
        capacity={},
        observed_at=_NOW,
        revision=1,
    )


def _change(action: str, package=None) -> DeploymentPlanChange:
    return DeploymentPlanChange(
        action=action,
        component_ref="skill:test_worker",
        node_id="node-a",
        target_package_digest=None if package is None else package.ref.digest,
        reason="test",
        phases=("cordon", "drain", "deactivate", "remove")
        if action == "remove"
        else ("fetch", "verify", "stage", "activate", "health", "commit"),
    )


def test_local_adapter_materializes_exact_component_and_removes_only_component(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    release = _release(package)
    store = ContentAddressedPackageStore(tmp_path / "packages")
    adapter = LocalComponentDeploymentAdapter(
        local_node_id="node-a",
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
        package_store=store,
        fetch_package=lambda ref: package.archive_bytes,
        hooks=NoopComponentLifecycleHooks(),
    )
    change = _change("install", package)
    receipts: dict[str, Mapping[str, Any]] = {}
    for phase in change.phases:
        receipts[phase] = adapter.execute_phase(
            phase=phase,
            node=_node("node-a"),
            change=change,
            desired=_desired(release),
            release_plan=release,
            package=package.ref,
            current_activation=None,
            idempotency_key=f"install:test_worker:{phase}",
            attempt=1,
        )
    target = tmp_path / "workspace" / "skills" / "test_worker"
    assert (target / "skill.yaml").is_file()
    assert receipts["verify"]["package_digest"] == package.ref.digest
    assert receipts["health"]["ready"] is True

    external = tmp_path / "workspace" / "media" / "original.mp3"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"original")
    removal = _change("remove")
    for phase in removal.phases:
        adapter.execute_phase(
            phase=phase,
            node=_node("node-a"),
            change=removal,
            desired=_desired(release),
            release_plan=release,
            package=None,
            current_activation=None,
            idempotency_key=f"remove:test_worker:{phase}",
            attempt=1,
        )
    assert not target.exists()
    assert external.read_bytes() == b"original"


class TimeoutTransport:
    def execute_component_phase(self, **kwargs: Any) -> Mapping[str, Any]:
        raise TimeoutError("ack lost")


def test_remote_timeout_is_explicitly_uncertain(tmp_path: Path) -> None:
    package = _package(tmp_path)
    release = _release(package)
    local = LocalComponentDeploymentAdapter(
        local_node_id="node-a",
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
        package_store=ContentAddressedPackageStore(tmp_path / "packages"),
        fetch_package=lambda ref: package.archive_bytes,
        hooks=NoopComponentLifecycleHooks(),
    )
    routing = RoutingComponentDeploymentAdapter(
        local_node_id="node-a", local=local, remote=TimeoutTransport()
    )
    with pytest.raises(UncertainDeploymentPhaseError):
        routing.execute_phase(
            phase="fetch",
            node=_node("node-b"),
            change=replace_node(_change("install", package), "node-b"),
            desired=_desired(release),
            release_plan=release,
            package=package.ref,
            current_activation=None,
            idempotency_key="remote:test_worker:fetch",
            attempt=1,
        )


def replace_node(change: DeploymentPlanChange, node_id: str) -> DeploymentPlanChange:
    from dataclasses import replace

    return replace(change, node_id=node_id)
