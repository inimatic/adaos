from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    ACTIVATION_PHASES,
    ActivationError,
    ActivationReplayBlocked,
    ContentAddressedPackageStore,
    PackageCatalog,
    WorkspaceActivationManager,
    build_artifact_package,
    build_project_release,
)


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )


def _built_scenario(root: Path, *, version: str, marker: str):
    scenario = root / f"source-{marker}"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        f"id: recipes\nversion: {version}\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(
        json.dumps({"marker": marker}) + "\n",
        encoding="utf-8",
    )
    return build_artifact_package(scenario, kind="scenario", source_ref=_source())


def _plan(built, *, permissions=(), migrations=()):
    return build_project_release(
        project_id="recipes",
        version=built.ref.version,
        source_ref=_source(),
        components=(built.ref,),
        catalog=PackageCatalog(),
        permissions=permissions,
        migrations=migrations,
    )


def _manager(tmp_path: Path):
    store = ContentAddressedPackageStore(tmp_path / "package-store")
    manager = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=store,
        state_root=tmp_path / "state",
    )
    return store, manager


def test_activation_installs_empty_workspace_and_is_idempotent(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="one")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)

    result = manager.activate(
        _plan(built),
        idempotency_key="install-recipes-1.0.0",
        health_check=lambda lock: lock.slots[0].release == "recipes@1.0.0",
    )

    target = tmp_path / "workspace" / "scenarios" / "recipes"
    assert json.loads((target / "webui.json").read_text(encoding="utf-8")) == {"marker": "one"}
    assert result.workspace_lock.lock_revision == 1
    assert manager.load_lock() == result.workspace_lock
    assert list((tmp_path / "workspace" / ".adaos" / "lock-history").glob("*.json"))
    operation = json.loads(manager.operation_path(result.operation_id).read_text(encoding="utf-8"))
    assert [event["phase"] for event in operation["events"]] == list(ACTIVATION_PHASES)
    assert operation["permission_decision"]["reason"] == "no_introduced_permissions"
    assert operation["migration_execution"]["status"] == "not_required"

    replay = manager.activate(_plan(built), idempotency_key="install-recipes-1.0.0")
    assert replay.idempotent_replay is True
    assert replay.workspace_lock.lock_revision == 1


def test_failed_health_rolls_back_files_and_lock_and_cannot_auto_replay(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="one")
    second = _built_scenario(tmp_path, version="1.1.0", marker="two")
    store, manager = _manager(tmp_path)
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    initial = manager.activate(_plan(first), idempotency_key="initial")

    with pytest.raises(ActivationError, match="health check failed"):
        manager.activate(
            _plan(second),
            idempotency_key="upgrade-fails",
            health_check=lambda lock: False,
        )

    target = tmp_path / "workspace" / "scenarios" / "recipes"
    assert json.loads((target / "webui.json").read_text(encoding="utf-8")) == {"marker": "one"}
    assert manager.load_lock() == initial.workspace_lock
    assert not list((tmp_path / "state" / "artifact_pipeline" / "backups").rglob("recipes"))

    with pytest.raises(ActivationReplayBlocked, match="explicitly new idempotency key"):
        manager.activate(_plan(second), idempotency_key="upgrade-fails")


@pytest.mark.parametrize("failed_phase", ACTIVATION_PHASES)
def test_failure_at_each_activation_phase_leaves_no_partial_first_install(
    tmp_path: Path,
    failed_phase: str,
) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker=failed_phase)
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)

    def fail(phase: str) -> None:
        if phase == failed_phase:
            raise RuntimeError(f"interrupt at {phase}")

    with pytest.raises(ActivationError, match=f"interrupt at {failed_phase}"):
        manager.activate(
            _plan(built),
            idempotency_key=f"failure-{failed_phase}",
            phase_hook=fail,
        )

    assert manager.load_lock() is None
    assert not (tmp_path / "workspace" / "scenarios" / "recipes").exists()


def test_activation_fetches_missing_package_once_and_verifies_reference(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="fetch")
    store, manager = _manager(tmp_path)
    fetched: list[str] = []

    result = manager.activate(
        _plan(built),
        idempotency_key="fetch-install",
        fetch_package=lambda ref: fetched.append(ref.digest) or built.archive_bytes,
    )

    assert result.status == "completed"
    assert fetched == [built.ref.digest]
    assert store.has(built.ref.digest)


def test_introduced_permissions_require_an_explicit_approval(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="permission")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)
    plan = _plan(built, permissions=("shopping.read",))

    with pytest.raises(ActivationError, match="no explicit permission decision"):
        manager.activate(plan, idempotency_key="permission-denied-by-default")

    result = manager.activate(
        plan,
        idempotency_key="permission-approved",
        permission_decision={"approved": True, "actor": "user:test"},
    )

    assert result.status == "completed"
    operation = json.loads(manager.operation_path(result.operation_id).read_text(encoding="utf-8"))
    assert operation["permission_plan"]["introduced"] == ["shopping.read"]
    assert operation["permission_decision"] == {"approved": True, "actor": "user:test"}


def test_irreversible_migration_is_rejected_before_staging(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="2.0.0", marker="irreversible")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)
    migration = {
        "id": "recipes-schema-1-to-2",
        "from_schema": 1,
        "to_schema": 2,
        "rollback": {"supported": False},
    }

    with pytest.raises(ActivationError, match="deferred attended workflow"):
        manager.activate(
            _plan(built, migrations=(migration,)),
            idempotency_key="irreversible-migration",
        )

    assert manager.load_lock() is None
    assert not (tmp_path / "workspace" / "scenarios" / "recipes").exists()


def test_reversible_migration_executes_once_and_rolls_back_after_health_failure(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="migration-old")
    second = _built_scenario(tmp_path, version="1.1.0", marker="migration-new")
    store, manager = _manager(tmp_path)
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    initial = manager.activate(_plan(first), idempotency_key="migration-initial")
    migration = {
        "id": "recipes-schema-1-to-2",
        "from_schema": 1,
        "to_schema": 2,
        "rollback": {"supported": True, "procedure_ref": "migration/2-to-1"},
    }
    executions: list[dict] = []
    rollbacks: list[dict] = []
    reloads: list[str] = []

    with pytest.raises(ActivationError, match="health check failed"):
        manager.activate(
            _plan(second, migrations=(migration,)),
            idempotency_key="migration-health-failure",
            migration_executor=lambda request: executions.append(dict(request))
            or {"status": "completed", "checkpoint": "snapshot:recipes-before-v2"},
            migration_rollback=lambda request: rollbacks.append(dict(request))
            or {"status": "rolled_back", "checkpoint": "snapshot:recipes-before-v2"},
            reload_runtime=lambda lock: reloads.append(lock.to_dict()["lock_digest"]),
            health_check=lambda _lock: False,
        )

    assert len(executions) == 1
    assert len(rollbacks) == 1
    assert manager.load_lock() == initial.workspace_lock
    assert reloads[-1] == initial.workspace_lock.to_dict()["lock_digest"]
    target = tmp_path / "workspace" / "scenarios" / "recipes" / "webui.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"marker": "migration-old"}


def test_unknown_migration_result_is_not_replayed_and_requires_reconciliation(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="2.0.0", marker="migration-uncertain")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)
    migration = {
        "id": "recipes-schema-1-to-2",
        "from_schema": 1,
        "to_schema": 2,
        "rollback": {"supported": True, "procedure_ref": "migration/2-to-1"},
    }
    executions: list[str] = []

    def timeout_after_dispatch(request: dict) -> dict:
        executions.append(request["operation_id"])
        raise TimeoutError("migration outcome unavailable")

    with pytest.raises(ActivationError, match="migration outcome unavailable"):
        manager.activate(
            _plan(built, migrations=(migration,)),
            idempotency_key="migration-uncertain",
            migration_executor=timeout_after_dispatch,
            migration_rollback=lambda _request: {"status": "rolled_back"},
        )

    assert len(executions) == 1
    with pytest.raises(ActivationReplayBlocked, match="explicitly new idempotency key"):
        manager.activate(
            _plan(built, migrations=(migration,)),
            idempotency_key="migration-uncertain",
            migration_executor=timeout_after_dispatch,
            migration_rollback=lambda _request: {"status": "rolled_back"},
        )
    operation_id = manager.operation_id("migration-uncertain")
    with pytest.raises(ActivationReplayBlocked, match="one-shot reconciliation"):
        manager.recover_interrupted(operation_id)

    reconciliations: list[str] = []
    recovered = manager.recover_interrupted(
        operation_id,
        migration_reconciler=lambda operation: reconciliations.append(operation["operation_id"])
        or {"status": "rolled_back", "evidence": "migration-log:42"},
    )

    assert recovered["status"] == "recovered"
    assert reconciliations == [operation_id]
    assert manager.load_lock() is None


def test_explicit_recovery_rolls_back_interrupted_journal_without_replaying(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="recovery")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)
    operation_id = manager.operation_id("interrupted")
    operation_path = manager.operation_path(operation_id)
    operation_path.parent.mkdir(parents=True)
    operation_path.write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.activation_operation.v1",
                "operation_id": operation_id,
                "idempotency_key": "interrupted",
                "release_digest": _plan(built).release.release_digest,
                "status": "running",
                "phase": "stage",
                "previous_lock": None,
                "moves": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ActivationReplayBlocked):
        manager.activate(_plan(built), idempotency_key="interrupted")

    recovered = manager.recover_interrupted(operation_id)
    assert recovered["status"] == "recovered"
    assert manager.load_lock() is None
