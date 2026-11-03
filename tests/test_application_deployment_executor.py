from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
    ResolvedDependency,
)
from adaos.domain.project_deployment import ComponentActivation, NodeInventoryRecord
from adaos.services.applications.deployment_executor import (
    ApplicationDataSnapshotStore,
    ApplicationDeploymentExecutor,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.packages import build_artifact_package
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


def _release_with_shared_dependency() -> ReleasePlan:
    scenario = ArtifactPackageRef(
        kind="scenario",
        artifact_id="app",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        manifest_digest="sha256:" + "f" * 64,
        source_ref=SOURCE,
    )
    provider = ArtifactPackageRef(
        kind="skill",
        artifact_id="shared_mail_provider",
        version="1.0.0",
        digest="sha256:" + "c" * 64,
        manifest_digest="sha256:" + "e" * 64,
        source_ref=SOURCE,
    )
    release = ProjectRelease(
        project_id="app",
        version="1.0.0",
        source_ref=SOURCE,
        components=(scenario,),
        resolved_dependencies=(
            ResolvedDependency(
                kind="skill",
                artifact_id="shared_mail_provider",
                version="1.0.0",
                package_digest=provider.digest,
                version_spec="^1",
            ),
        ),
        validation_evidence=({"status": "passed"},),
    ).seal()
    return ReleasePlan(
        release=release,
        packages=(scenario, provider),
        bindings=(),
        reverse_consumers={"skill:shared_mail_provider": ("scenario:app",)},
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
        self.calls: list[tuple[str, str]] = []

    def execute_phase(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append((kwargs["change"].component_ref, kwargs["phase"]))
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


def test_application_release_update_reuses_exact_component_package(
    tmp_path: Path,
) -> None:
    first = _release("1.0.0", "a")
    reused_package = first.packages[0]
    second_release = ProjectRelease(
        project_id="app",
        version="1.0.1",
        source_ref=SOURCE,
        components=(reused_package,),
        validation_evidence=({"status": "passed", "revision": 2},),
    ).seal()
    second = ReleasePlan(
        release=second_release,
        packages=(reused_package,),
        bindings=(),
        reverse_consumers={},
    )
    adapter = Adapter()
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(first, second),
        inventory=Inventory(),
        adapter=adapter,
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)

    executor(_plan("install", first))
    adapter.calls.clear()
    updated = executor(
        _plan("update", second, source_digest=str(first.release.release_digest))
    )

    assert updated["status"] == "active"
    change = updated["deployment_plan"]["changes"][0]
    assert change["action"] == "noop"
    assert change["phases"] == ["observe"]
    assert adapter.calls == [("scenario:app", "observe")]
    operation_id = updated["deployment_operation"]["operation_id"]
    history = runtime.store._operation_root(operation_id) / "history"
    # accepted -> running -> one completed component -> succeeded.  A transient
    # component/phase-running marker and a separate noop receipt checkpoint must
    # not rewrite the full aggregate.
    assert len(list(history.glob("*.json"))) == 4


def test_executor_removes_legacy_installation_without_project_deployment(
    tmp_path: Path,
) -> None:
    release = _release("1.0.0", "a")
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)

    removed = executor(_plan("remove", release, data_policy="retain"))

    assert removed == {
        "ok": True,
        "status": "removed",
        "deployment": None,
        "deployment_operations": [],
        "snapshot_receipt": None,
        "compatibility_removal": "installation_without_project_deployment",
    }
    with pytest.raises(FileNotFoundError):
        runtime.store.get_deployment("application-deployment:app_test")


def test_executor_refuses_unadmitted_cbs_before_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release("1.0.0", "a")
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    monkeypatch.setattr(
        executor,
        "_native_cbs_admission",
        lambda _plan: {
            "schema": "adaos.application.cbs_admission.v1",
            "status": "unresolved",
        },
    )

    result = executor(_plan("install", release))

    assert result["status"] == "failed"
    assert result["reason"] == "native_cbs_admission_required"
    with pytest.raises(FileNotFoundError):
        runtime.store.get_deployment("application-deployment:app_test")


def test_executor_compensates_deployment_when_native_cbs_lock_is_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release("1.0.0", "a")
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    monkeypatch.setattr(
        executor,
        "_native_cbs_admission",
        lambda _plan: {
            "schema": "adaos.application.cbs_admission.v1",
            "status": "admitted",
            "application_ref": "application:app_test",
        },
    )

    result = executor(_plan("install", release))

    assert result["status"] == "failed"
    assert result["reason"] == "native_cbs_workspace_activation_failed"
    assert result["deployment_compensation"]["state"] == "succeeded"
    assert (
        runtime.store.get_deployment("application-deployment:app_test").status
        == "removed"
    )


def test_executor_prefetches_and_verifies_package_for_planning_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "provider"
    source.mkdir()
    (source / "skill.yaml").write_text(
        "name: shared_mail_provider\nversion: 2.0.0\n", encoding="utf-8"
    )
    built = build_artifact_package(source, kind="skill", source_ref=SOURCE)

    class _Releases:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch_package(self, package: ArtifactPackageRef) -> bytes:
            self.calls.append(package.digest)
            return built.archive_bytes

    releases = _Releases()
    executor = ApplicationDeploymentExecutor(
        runtime=SimpleNamespace(releases=releases), state_dir=tmp_path
    )

    executor.ensure_verified_package(built.ref)
    executor.ensure_verified_package(built.ref)

    assert releases.calls == [built.ref.digest]
    assert executor.package_store.verify(built.ref.digest).ref == built.ref


def test_executor_refreshes_legacy_admission_before_workspace_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import adaos.services.applications.cbs as cbs_module
    import adaos.services.applications.cbs_admission as admission_module

    release = _release("1.0.0", "a")
    source = {"application_ref": "application:app_test"}
    legacy = {
        "schema": "adaos.application.cbs_admission.v1",
        "application_ref": "application:app_test",
        "status": "admitted",
    }
    refreshed = {
        **legacy,
        "binding_instance_records": [{"binding_instance_ref": "binding:test"}],
    }
    admissions = SimpleNamespace(
        find_by_project_release=lambda *_args, **_kwargs: legacy,
        admit=lambda **kwargs: (admit_calls.append(kwargs) or refreshed),
    )
    admit_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        cbs_module,
        "ApplicationCBSService",
        lambda _state_dir: SimpleNamespace(
            inspect_requirement_source=lambda *_args, **_kwargs: source
        ),
    )
    monkeypatch.setattr(
        admission_module,
        "NativeApplicationCBSAdmissionService",
        lambda _state_dir: admissions,
    )
    executor = ApplicationDeploymentExecutor(
        runtime=SimpleNamespace(releases=Releases(release)),
        state_dir=tmp_path,
    )
    monkeypatch.setattr(executor, "ensure_verified_package", lambda _package: None)

    result = executor._native_cbs_admission(_plan("update", release))

    assert result == refreshed
    assert len(admit_calls) == 1
    assert admit_calls[0]["application_ref"] == "application:app_test"
    assert admit_calls[0]["workspace_ref"] == "workspace:home"


@pytest.mark.parametrize("status_code,error_code", [(404, "release_not_found"), (503, "registry_unavailable")])
def test_registry_failure_during_admission_has_no_deployment_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status_code: int, error_code: str
) -> None:
    import adaos.services.applications.cbs as cbs_module
    import adaos.services.applications.cbs_admission as admission_module
    from adaos.services.root.client import RootHttpError

    release = _release("1.0.0", "a")
    source = {"application_ref": "application:app_test"}
    monkeypatch.setattr(cbs_module, "ApplicationCBSService", lambda _state: SimpleNamespace(
        inspect_requirement_source=lambda *_args, **_kwargs: source))
    monkeypatch.setattr(admission_module, "NativeApplicationCBSAdmissionService", lambda _state: SimpleNamespace(
        find_by_project_release=lambda *_args, **_kwargs: None))

    class UnavailableReleases:
        def get_release(self, *_args):
            raise RootHttpError("errors." + error_code, status_code=status_code, error_code=error_code)

    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path), releases=UnavailableReleases(),
        inventory=Inventory(), adapter=Adapter(), local_node_id="node-local")
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)

    result = executor(_plan("install", release))

    assert result["status"] == "failed"
    assert result["reason"] == "native_cbs_admission_required"
    assert result["cbs_admission"]["error_type"] == "RootHttpError"
    assert result["cbs_admission"]["message"] == "errors." + error_code
    with pytest.raises(FileNotFoundError):
        runtime.store.get_deployment("application-deployment:app_test")
    assert not tuple((runtime.store.root / "operations").glob("*/current.json"))


def test_executor_materializes_resolved_dependency_from_exact_release_closure(
    tmp_path: Path,
) -> None:
    release = _release_with_shared_dependency()
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    plan = _plan("install", release)
    plan["components"].append(
        {
            "component_ref": "skill:shared_mail_provider",
            "package_digest": release.packages[1].digest,
            "lifecycle": "shared",
            "materialization": "activate",
        }
    )

    installed = executor(plan)

    assert installed["status"] == "active"
    activations, cursor = runtime.store.list_activations(
        deployment_id="application-deployment:app_test",
        limit=20,
    )
    assert cursor is None
    assert {
        (item.component_ref, item.package_digest, item.status)
        for item in activations
    } == {
        ("scenario:app", release.packages[0].digest, "active"),
        ("skill:shared_mail_provider", release.packages[1].digest, "active"),
    }


def test_executor_retains_shared_dependency_until_last_application_reference(
    tmp_path: Path,
) -> None:
    release = _release_with_shared_dependency()
    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(release),
        inventory=Inventory(),
        adapter=Adapter(),
        local_node_id="node-local",
    )
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path)
    publisher_plan = _plan("install", release)
    publisher_plan["application_id"] = "provider_publisher"
    publisher_plan["idempotency_key"] = "publisher-install"
    publisher_plan["components"].append(
        {
            "component_ref": "skill:shared_mail_provider",
            "package_digest": release.packages[1].digest,
            "lifecycle": "shared",
            "materialization": "activate",
        }
    )
    assert executor(publisher_plan)["status"] == "active"

    publisher_remove = {
        **publisher_plan,
        "kind": "remove",
        "idempotency_key": "publisher-remove",
        "removal": {
            "components": [
                {
                    "component_ref": "scenario:app",
                    "package_digest": release.packages[0].digest,
                    "remove_package": True,
                },
                {
                    "component_ref": "skill:shared_mail_provider",
                    "package_digest": release.packages[1].digest,
                    "remove_package": False,
                },
            ]
        },
    }
    assert executor(publisher_remove)["status"] == "removed"
    retained = next(
        item
        for item in runtime.store.list_activations(limit=20)[0]
        if item.component_ref == "skill:shared_mail_provider"
    )
    assert retained.status == "active"

    consumer_plan = _plan("install", release)
    consumer_plan["application_id"] = "provider_consumer"
    consumer_plan["idempotency_key"] = "consumer-install"
    consumer_plan["components"].append(
        {
            "component_ref": "skill:shared_mail_provider",
            "package_digest": release.packages[1].digest,
            "lifecycle": "shared",
            "materialization": "reuse",
        }
    )
    assert executor(consumer_plan)["status"] == "active"
    consumer_remove = {
        **consumer_plan,
        "kind": "remove",
        "idempotency_key": "consumer-remove",
        "removal": {
            "components": [
                {
                    "component_ref": "scenario:app",
                    "package_digest": release.packages[0].digest,
                    "remove_package": True,
                },
                {
                    "component_ref": "skill:shared_mail_provider",
                    "package_digest": release.packages[1].digest,
                    "remove_package": True,
                },
            ]
        },
    }

    assert executor(consumer_remove)["status"] == "removed"
    assert runtime.store.get_activation(retained.activation_id).status == "removed"


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
    options = executor.placement_options(
        "app_test",
        "skill:app_worker",
        limit=10,
    )
    assert options["desired_revision"] == 3
    assert options["candidates"][0]["node_id"] == "node-local"

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


def _local_guarded_executor(tmp_path):
    from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
    from adaos.services.project_deployment import LocalComponentDeploymentAdapter
    from adaos.services.applications.runtime_integrity import assert_managed_skill_activation
    from adaos.services.artifact_pipeline.trial_activation import load_workspace_lock

    store = ContentAddressedPackageStore(tmp_path / "artifact_pipeline/packages")
    plans = []
    for version in ("1.0.0", "1.1.0"):
        source = tmp_path / "sources" / version
        source.mkdir(parents=True)
        (source / "skill.yaml").write_text("name: worker\nversion: " + version + "\n", encoding="utf-8")
        (source / "handler.py").write_text("VERSION = '" + version + "'\n", encoding="utf-8")
        package = build_artifact_package(source, kind="skill", source_ref=SOURCE)
        store.put(package.archive_bytes, expected_digest=package.ref.digest)
        release = ProjectRelease(project_id="app", version=version, source_ref=SOURCE,
            components=(package.ref,), validation_evidence=({"status": "passed"},)).seal()
        plans.append(ReleasePlan(release=release, packages=(package.ref,), bindings=(), reverse_consumers={}))

    workspace = tmp_path / "workspace"
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path, workspace_dir=lambda: workspace))

    class Hooks:
        active = None
        fail_version = None
        fail_activation = None
        calls = []

        def activate(self, **value):
            assert_managed_skill_activation(ctx, value["component_id"], value["package_digest"])
            lock = load_workspace_lock(workspace / ".adaos/workspace.lock.json")
            assert next(p.digest for p in lock.components if p.key == "skill:worker") == value["package_digest"]
            self.active = value["version"]
            self.calls.append(value["version"])
            if self.fail_activation == value["version"]:
                raise RuntimeError("activation acknowledgement lost")
            return {"activated": True}

        def health(self, **value):
            return {"ready": self.active == value["version"] and self.active != self.fail_version}

    hooks = Hooks()
    adapter = LocalComponentDeploymentAdapter(local_node_id="node-local", workspace_root=workspace,
        state_root=tmp_path, package_store=store, fetch_package=lambda p: store.read(p.digest), hooks=hooks)
    runtime = ProjectDeploymentRuntime(store=ProjectDeploymentStore(state_dir=tmp_path),
        releases=Releases(*plans), inventory=Inventory(), adapter=adapter, local_node_id="node-local")
    executor = ApplicationDeploymentExecutor(runtime=runtime, state_dir=tmp_path, workspace_root=workspace)

    def plan(kind, index):
        value = _plan(kind, plans[index], source_digest=plans[0].release.release_digest if kind == "update" else None)
        value["components"] = [{"component_ref": "skill:worker", "package_digest": plans[index].packages[0].digest,
            "lifecycle": "bound"}]
        return value

    return executor, hooks, plans, plan


def test_local_reviewed_update_commits_lock_before_managed_runtime_hook(tmp_path):
    executor, hooks, plans, plan = _local_guarded_executor(tmp_path)
    installed = executor(plan("install", 0))
    assert installed["status"] == "active", installed
    updated = executor(plan("update", 1))
    assert updated["status"] == "active", updated
    assert updated["workspace_activation"]["status"] == "completed"
    assert hooks.active == "1.1.0"
    assert hooks.calls == ["1.0.0", "1.1.0"]


def test_local_health_failure_restores_lock_source_runtime_and_data(tmp_path):
    from adaos.services.artifact_pipeline.trial_activation import load_workspace_lock
    executor, hooks, plans, plan = _local_guarded_executor(tmp_path)
    assert executor(plan("install", 0))["status"] == "active"
    old_lock = load_workspace_lock(executor.workspace_root / ".adaos/workspace.lock.json").to_dict()
    data = executor.snapshots.data_root / "app_test/state.json"
    data.parent.mkdir(parents=True);data.write_text('retained', encoding="utf-8")
    hooks.fail_version = "1.1.0"
    result = executor(plan("update", 1))
    assert result["status"] == "failed", result
    assert result["restore_receipt"]["status"] == "restored"
    assert hooks.active == "1.0.0"
    assert load_workspace_lock(executor.workspace_root / ".adaos/workspace.lock.json").to_dict() == old_lock
    assert data.read_text() == 'retained'
    assert "1.0.0" in (executor.workspace_root / "skills/worker/skill.yaml").read_text()


def test_local_unknown_compensation_does_not_repeat_hooks_or_restore_data(tmp_path, monkeypatch):
    executor, hooks, plans, plan = _local_guarded_executor(tmp_path)
    assert executor(plan("install", 0))["status"] == "active"
    hooks.fail_version = "1.1.0"
    hooks.fail_activation = "1.0.0"
    restored = []
    monkeypatch.setattr(executor.snapshots, "restore", lambda ref: restored.append(ref))
    result = executor(plan("update", 1))
    assert result["status"] == "unknown", result
    assert result["compensation_error"]
    assert hooks.calls == ["1.0.0", "1.1.0", "1.0.0"]
    assert restored == []


def test_local_failure_before_mutation_restores_desired_without_running_hooks(tmp_path, monkeypatch):
    from adaos.services.artifact_pipeline.activation import WorkspaceActivationManager, ActivationError
    executor, hooks, plans, plan = _local_guarded_executor(tmp_path)
    assert executor(plan("install", 0))["status"] == "active"
    def fail(*args, **kwargs):
        raise ActivationError("reviewed lock no longer admissible")
    monkeypatch.setattr(WorkspaceActivationManager, "activate", fail)
    result = executor(plan("update", 1))
    assert result["status"] == "failed", result
    assert hooks.calls == ["1.0.0"]
    desired = executor.runtime.store.get_deployment("application-deployment:app_test")
    assert desired.release_digest == plans[0].release.release_digest


@pytest.mark.parametrize("drifted", [False, True])
def test_local_noop_observes_healthy_pointer_and_repairs_only_real_runtime_drift(tmp_path, drifted):
    executor, hooks, plans, plan = _local_guarded_executor(tmp_path)
    assert executor(plan("install", 0))["status"] == "active"
    release = ProjectRelease(project_id="app", version="1.2.0", source_ref=SOURCE,
        components=plans[0].packages, validation_evidence=({"status": "passed"},)).seal()
    successor = ReleasePlan(release=release, packages=plans[0].packages, bindings=(), reverse_consumers={})
    executor.runtime.releases.plans[release.release_digest] = successor
    request = _plan("update", successor, source_digest=plans[0].release.release_digest)
    request["components"] = plan("install", 0)["components"]
    if drifted:
        hooks.active = "stale-native-pointer"
    result = executor(request)
    assert result["status"] == "active", result
    assert hooks.active == "1.0.0"
    assert hooks.calls == (["1.0.0", "1.0.0"] if drifted else ["1.0.0"])
    changes = result["deployment_plan"]["changes"]
    assert changes[0]["action"] == "noop"
