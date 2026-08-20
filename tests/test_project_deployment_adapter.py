from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Mapping

import pytest
import httpx

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
    HttpNodeDeploymentTransport,
    MemberLinkNodeDeploymentTransport,
    NoopComponentLifecycleHooks,
    ProjectDeploymentExecutionError,
    RoutingComponentDeploymentAdapter,
    UncertainDeploymentPhaseError,
    execute_remote_component_phase,
    register_local_deployment_receiver,
)
from adaos.services.project_deployment.execution import component_activation_id
from adaos.services.project_deployment.store import ProjectDeploymentStore


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


def test_remote_receiver_revalidates_identity_and_package_digest(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    release = _release(package)
    store = ContentAddressedPackageStore(tmp_path / "remote-packages")
    adapter = LocalComponentDeploymentAdapter(
        local_node_id="node-b",
        workspace_root=tmp_path / "remote-workspace",
        state_root=tmp_path / "remote-state",
        package_store=store,
        fetch_package=lambda ref: store.read(ref.digest),
        hooks=NoopComponentLifecycleHooks(),
    )
    register_local_deployment_receiver(adapter, node_id="node-b")
    change = replace_node(_change("install", package), "node-b")
    payload = {
        "schema": "adaos.project.remote_component_phase.v1",
        "source_node_id": "node-a",
        "target_node_id": "node-b",
        "phase": "fetch",
        "node": _node("node-b").to_dict(),
        "change": change.to_dict(),
        "desired": _desired(release).to_dict(),
        "release_plan": {
            "schema": "adaos.artifact.release_plan.v1",
            **release.explain(),
        },
        "package": package.ref.to_dict(),
        "current_activation": None,
        "idempotency_key": "remote:test-worker:fetch",
        "attempt": 1,
        "package_archive_b64": base64.b64encode(package.archive_bytes).decode("ascii"),
    }

    result = execute_remote_component_phase(payload)

    assert result["schema"] == "adaos.project.remote_component_phase_result.v1"
    assert result["target_node_id"] == "node-b"
    assert store.verify(package.ref.digest).ref == package.ref

    committed = execute_remote_component_phase(
        {
            **payload,
            "phase": "commit",
            "idempotency_key": "remote:test-worker:commit",
            "package_archive_b64": None,
        }
    )
    activation_store = ProjectDeploymentStore(state_dir=tmp_path / "remote-state")
    desired = _desired(release)
    activation = activation_store.get_activation(
        component_activation_id(desired, change, package.ref)
    )

    assert committed["receipt"]["committed"] is True
    assert activation.status == "active"
    assert activation.node_id == "node-b"
    assert activation.release_digest == desired.release_digest
    assert activation.generation == desired.revision
    with pytest.raises(
        ProjectDeploymentExecutionError, match="target_identity_mismatch"
    ):
        execute_remote_component_phase({**payload, "target_node_id": "node-c"})
    register_local_deployment_receiver(None)


def test_http_transport_sends_exact_contract_and_marks_lost_ack_uncertain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    release = _release(package)
    change = replace_node(_change("install", package), "node-b")
    captured: dict[str, Any] = {}

    def successful_post(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={
                "schema": "adaos.project.remote_component_phase_result.v1",
                "target_node_id": "node-b",
                "phase": "fetch",
                "receipt": {"package_digest": package.ref.digest},
            },
        )

    monkeypatch.setattr(httpx, "post", successful_post)
    transport = HttpNodeDeploymentTransport(
        endpoint_resolver=lambda node_id: "http://node-b:8778",
        token_provider=lambda: "subnet-token",
        package_reader=lambda digest: package.archive_bytes,
        source_node_id="node-a",
    )
    receipt = transport.execute_component_phase(
        node_id="node-b",
        phase="fetch",
        node=_node("node-b"),
        change=change,
        desired=_desired(release),
        release_plan=release,
        package=package.ref,
        current_activation=None,
        idempotency_key="remote:test-worker:fetch",
        attempt=1,
    )

    assert receipt["package_digest"] == package.ref.digest
    assert captured["url"] == "http://node-b:8778/api/node/project-deployment/phase"
    assert captured["json"]["node"]["node_id"] == "node-b"
    assert captured["headers"]["X-AdaOS-Token"] == "subnet-token"

    def lost_ack(*args: Any, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", str(args[0]))
        raise httpx.ReadTimeout("ack lost", request=request)

    monkeypatch.setattr(httpx, "post", lost_ack)
    with pytest.raises(UncertainDeploymentPhaseError, match="timed out after dispatch"):
        transport.execute_component_phase(
            node_id="node-b",
            phase="fetch",
            node=_node("node-b"),
            change=change,
            desired=_desired(release),
            release_plan=release,
            package=package.ref,
            current_activation=None,
            idempotency_key="remote:test-worker:fetch-uncertain",
            attempt=1,
        )


def test_member_link_transport_sends_exact_contract_and_marks_lost_ack_uncertain(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    release = _release(package)
    change = replace_node(_change("install", package), "node-b")
    calls: list[dict[str, Any]] = []

    def rpc_call(node_id: str, **kwargs: Any) -> Mapping[str, Any]:
        calls.append({"node_id": node_id, **kwargs})
        return {
            "schema": "adaos.project.remote_component_phase_result.v1",
            "target_node_id": "node-b",
            "phase": "fetch",
            "receipt": {"package_digest": package.ref.digest},
        }

    transport = MemberLinkNodeDeploymentTransport(
        rpc_call=rpc_call,
        package_reader=lambda _digest: package.archive_bytes,
        source_node_id="node-a",
    )
    receipt = transport.execute_component_phase(
        node_id="node-b",
        phase="fetch",
        node=_node("node-b"),
        change=change,
        desired=_desired(release),
        release_plan=release,
        package=package.ref,
        current_activation=None,
        idempotency_key="member:test-worker:fetch",
        attempt=1,
    )

    assert receipt["package_digest"] == package.ref.digest
    assert calls[0]["method"] == "project.deployment.phase"
    assert calls[0]["params"]["target_node_id"] == "node-b"
    assert calls[0]["params"]["package_archive_b64"]

    def lost_ack(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise TimeoutError("lost ack")

    transport.rpc_call = lost_ack
    with pytest.raises(UncertainDeploymentPhaseError, match="timed out after dispatch"):
        transport.execute_component_phase(
            node_id="node-b",
            phase="health",
            node=_node("node-b"),
            change=change,
            desired=_desired(release),
            release_plan=release,
            package=package.ref,
            current_activation=None,
            idempotency_key="member:test-worker:health",
            attempt=1,
        )
