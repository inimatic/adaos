from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
)
from adaos.domain.project_deployment import ComponentActivation, NodeInventoryRecord
from adaos.services.applications.deployment_executor import (
    ApplicationDataSnapshotStore,
    ApplicationDeploymentExecutor,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.project_deployment import (
    ProjectDeploymentExecutionError,
    ProjectDeploymentRuntime,
    ProjectDeploymentStore,
)


SOURCE = ArtifactSourceRef(
    forge="github",
    repository="inimatic/app",
    revision="0123456789abcdef0123456789abcdef01234567",
    path_scope=("scenarios/app/",),
)


def _release(version: str, character: str) -> ReleasePlan:
    digest = "sha256:" + character * 64
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="app",
        version=version,
        digest=digest,
        manifest_digest="sha256:" + "f" * 64,
        source_ref=SOURCE,
    )
    release = ProjectRelease(
        project_id="app",
        version=version,
        source_ref=SOURCE,
        components=(package,),
        validation_evidence=({"status": "passed"},),
    ).seal()
    return ReleasePlan(
        release=release, packages=(package,), bindings=(), reverse_consumers={}
    )


def _multi_release(version: str, character: str) -> ReleasePlan:
    scenario = ArtifactPackageRef(
        kind="scenario",
        artifact_id="app",
        version=version,
        digest="sha256:" + character * 64,
        manifest_digest="sha256:" + "f" * 64,
        source_ref=SOURCE,
    )
    worker = ArtifactPackageRef(
        kind="skill",
        artifact_id="app_worker",
        version=version,
        digest="sha256:" + "d" * 64,
        manifest_digest="sha256:" + "e" * 64,
        source_ref=SOURCE,
    )
    release = ProjectRelease(
        project_id="app",
        version=version,
        source_ref=SOURCE,
        components=(scenario, worker),
        validation_evidence=({"status": "passed"},),
    ).seal()
    return ReleasePlan(
        release=release,
        packages=(scenario, worker),
        bindings=(),
        reverse_consumers={},
    )


class Releases:
    def __init__(self, *plans: ReleasePlan) -> None:
        self.plans = {str(plan.release.release_digest): plan for plan in plans}

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        assert project_id == "app"
        return self.plans[release_digest]


class Inventory:
    def list_nodes(self, subnet_id: str):
        return (
            NodeInventoryRecord(
                node_id="node-local",
                subnet_id=subnet_id,
                trust_state="trusted",
                online=True,
                architecture="x86_64",
                runtime_version="1.0.0",
                capabilities=("project.activate",),
                protocols={},
                labels={},
                capacity={},
                endpoints=(),
                observed_at="2026-09-05T12:00:00+00:00",
                revision=1,
            ),
        )


class Adapter:
    def __init__(self) -> None:
        self.fail_health = False

    def execute_phase(self, **kwargs: Any) -> Mapping[str, Any]:
        if kwargs["phase"] == "health" and self.fail_health:
            raise ProjectDeploymentExecutionError("health check failed")
        if kwargs["phase"] == "health":
            return {"ready": True}
        return {"ok": True}


class PaginatedRuntime(ProjectDeploymentRuntime):
    def inspect(self, *args: Any, **kwargs: Any):
        kwargs["limit"] = min(int(kwargs.get("limit", 50)), 50)
        return super().inspect(*args, **kwargs)

    def remove(self, activation_id: str, **kwargs: Any):
        class Result:
            uncertain = False
            state = "succeeded"

            def to_dict(self) -> dict[str, Any]:
                return {"activation_id": activation_id, "state": self.state}

        return Result()


def _plan(
    kind: str,
    release: ReleasePlan,
    *,
    source_digest: str | None = None,
    data_policy: str = "retain",
) -> dict[str, Any]:
    return {
        "schema": "adaos.application.operation_plan.v1",
        "application_id": "app_test",
        "legacy_project_id": "app",
        "actor_ref": "user:owner",
        "subnet_ref": "subnet:home",
        "idempotency_key": f"{kind}-1",
        "kind": kind,
        "release_digest": str(release.release.release_digest),
        "components": [
            {
                "component_ref": "scenario:app",
                "package_digest": release.packages[0].digest,
                "lifecycle": "bound",
            }
        ],
        "data_policy": data_policy,
        "snapshot": {
            "required": kind == "update",
            "source_release_digest": source_digest,
            "consistency_boundary": "artifact_activation_transaction"
            if kind == "update"
            else None,
        },
    }


def test_executor_runs_install_update_remove_through_project_deployment(
    tmp_path: Path,
) -> None:
    first = _release("1.0.0", "a")
    second = _release("1.1.0", "b")
    adapter = Adapter()
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(first, second),
        inventory=Inventory(),
        adapter=adapter,
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)

    installed = executor(_plan("install", first))
    assert installed["status"] == "active"

    data_root = executor.snapshots.data_root / "app_test"
    data_root.mkdir(parents=True)
    (data_root / "state.json").write_text('{"version": 1}', encoding="utf-8")
    updated = executor(
        _plan("update", second, source_digest=str(first.release.release_digest))
    )
    assert updated["status"] == "active"
    assert updated["snapshot_receipt"]["file_count"] == 1

    removed = executor(_plan("remove", second, data_policy="snapshot_then_delete"))
    assert removed["status"] == "removed"
    assert removed["snapshot_receipt"]["file_count"] == 1
    assert not data_root.exists()
    assert (
        runtime.store.get_deployment("application-deployment:app_test").status
        == "removed"
    )


def test_executor_applies_reviewed_component_relocation_and_uninstall(
    tmp_path: Path,
) -> None:
    release = _multi_release("1.0.0", "a")
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    install_plan = _plan("install", release)
    install_plan["components"] = [
        {
            "component_ref": f"{package.kind}:{package.artifact_id}",
            "package_digest": package.digest,
            "lifecycle": "bound",
        }
        for package in release.packages
    ]
    assert executor(install_plan)["status"] == "active"
    assert executor.deployment_snapshot("app_test")["revision"] == 1

    relocation = {
        **install_plan,
        "kind": "relocate_component",
        "expected_revision": 1,
        "idempotency_key": "relocate-1",
        "placement_change": {
            "component_ref": "skill:app_worker",
            "target_node_id": "node-local",
            "effect": "relocate",
        },
    }
    assert executor(relocation)["status"] == "active"
    relocated = runtime.store.get_deployment("application-deployment:app_test")
    worker = next(
        item
        for item in relocated.placements
        if item.component_ref == "skill:app_worker"
    )
    assert worker.mode == "selected_nodes"
    assert worker.selected_node_ids == ("node-local",)

    removal = {
        **install_plan,
        "kind": "remove_component",
        "expected_revision": 2,
        "idempotency_key": "remove-component-2",
        "placement_change": {
            "component_ref": "skill:app_worker",
            "target_node_id": None,
            "effect": "uninstall",
        },
    }
    removed = executor(removal)
    assert not removed.get("warnings"), removed.get("warnings")
    assert removed["status"] == "active", removed
    desired = runtime.store.get_deployment("application-deployment:app_test")
    assert desired.revision == 3
    assert {item.component_ref: item.mode for item in desired.placements} == {
        "scenario:app": "singleton",
        "skill:app_worker": "disabled",
    }

    component_install = {
        **install_plan,
        "kind": "install_component",
        "expected_revision": 3,
        "idempotency_key": "install-component-3",
        "placement_change": {
            "component_ref": "skill:app_worker",
            "target_node_id": "node-local",
            "effect": "install",
        },
    }
    assert executor(component_install)["status"] == "active"
    reinstalled = runtime.store.get_deployment("application-deployment:app_test")
    worker = next(
        item
        for item in reinstalled.placements
        if item.component_ref == "skill:app_worker"
    )
    assert reinstalled.revision == 4
    assert worker.mode == "selected_nodes"
    assert worker.selected_node_ids == ("node-local",)


def test_failed_update_restores_data_and_previous_desired_release(
    tmp_path: Path,
) -> None:
    first = _release("1.0.0", "a")
    second = _release("1.1.0", "b")
    adapter = Adapter()
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(first, second),
        inventory=Inventory(),
        adapter=adapter,
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    assert executor(_plan("install", first))["ok"] is True
    data_root = executor.snapshots.data_root / "app_test"
    data_root.mkdir(parents=True)
    (data_root / "state.txt").write_text("before", encoding="utf-8")
    adapter.fail_health = True

    failed = executor(
        _plan("update", second, source_digest=str(first.release.release_digest))
    )

    assert failed["status"] == "failed"
    assert failed["restore_receipt"]["status"] == "restored"
    assert (data_root / "state.txt").read_text(encoding="utf-8") == "before"
    assert (
        runtime.store.get_deployment("application-deployment:app_test").release_digest
        == first.release.release_digest
    )


def test_snapshot_store_rejects_symlinked_data(tmp_path: Path) -> None:
    store = ApplicationDataSnapshotStore(tmp_path)
    data = store.data_root / "app_test"
    data.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        (data / "link.txt").symlink_to(target)
    except OSError:
        return
    try:
        store.create(
            "app_test",
            source_release_digest="sha256:" + "a" * 64,
            consistency_boundary="test",
        )
    except RuntimeError as exc:
        assert "symbolic" in str(exc)
    else:
        raise AssertionError("snapshot accepted a symbolic link")


def test_remove_enumerates_every_activation_page(tmp_path: Path) -> None:
    release = _release("1.0.0", "a")
    runtime = PaginatedRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    assert executor(_plan("install", release))["status"] == "active"
    for index in range(1, 75):
        runtime.store.put_activation(
            ComponentActivation(
                activation_id=f"activation-{index:03d}",
                deployment_id="application-deployment:app_test",
                component_ref=f"scenario:stale_{index}",
                node_id="node-local",
                release_digest=str(release.release.release_digest),
                package_digest=f"sha256:{index:064x}",
                generation=1,
                status="active",
                created_at="2026-09-05T12:00:00+00:00",
                updated_at="2026-09-05T12:00:00+00:00",
            )
        )

    removed = executor(_plan("remove", release))

    assert removed["status"] == "removed"
    assert len(removed["deployment_operations"]) == 75
