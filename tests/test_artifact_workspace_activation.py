from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, WorkspaceLock
from adaos.services.artifact_pipeline import (
    ACTIVATION_PHASES,
    ActivationConflictError,
    ActivationError,
    ActivationReplayBlocked,
    ContentAddressedPackageStore,
    DependencyRequirement,
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


def _built_skill(root: Path, *, version: str, marker: str):
    skill = root / f"skill-{marker}"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        f"name: shopping\nversion: {version}\n",
        encoding="utf-8",
    )
    (skill / "handler.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    return build_artifact_package(skill, kind="skill", source_ref=_source())


def _plan_with_skill(scenario, skill):
    return build_project_release(
        project_id="recipes",
        version=scenario.ref.version,
        source_ref=_source(),
        components=(scenario.ref,),
        catalog=PackageCatalog((skill.ref,)),
        requirements_by_package={
            scenario.ref.digest: (
                DependencyRequirement("skill", "shopping", skill.ref.version),
            )
        },
    )


def _manager(tmp_path: Path, *, delayed_verification_seconds: float = 30):
    store = ContentAddressedPackageStore(tmp_path / "package-store")
    manager = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=store,
        state_root=tmp_path / "state",
        delayed_verification_seconds=delayed_verification_seconds,
    )
    return store, manager


def _activate(manager: WorkspaceActivationManager, plan, **kwargs):
    if kwargs.get("reload_runtime") is None:
        kwargs.setdefault(
            "reload_policy",
            {
                "mode": "skip",
                "approved_by": "pytest.artifact_activation",
                "reason": "test Workspace has no attached runtime",
            },
        )
    if kwargs.get("health_check") is None:
        kwargs.setdefault(
            "health_policy",
            {
                "mode": "skip",
                "approved_by": "pytest.artifact_activation",
                "reason": "test case does not exercise live runtime health",
            },
        )
    return manager.activate(plan, **kwargs)


def test_activation_installs_empty_workspace_and_is_idempotent(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="one")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)

    result = _activate(manager,
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
    assert operation["reload_receipt"]["status"] == "skipped"
    assert operation["reload_receipt"]["approved_by"] == "pytest.artifact_activation"
    assert operation["health_receipt"]["status"] == "passed"
    assert operation["delayed_verification"]["status"] == "pending"
    assert operation["delayed_verification"]["expected_lock_digest"] == (
        result.workspace_lock.to_dict()["lock_digest"]
    )
    assert result.delayed_verification_id == operation["delayed_verification"]["observation_id"]

    replay = _activate(manager, _plan(built), idempotency_key="install-recipes-1.0.0")
    assert replay.idempotent_replay is True
    assert replay.workspace_lock.lock_revision == 1
    assert replay.delayed_verification_id == result.delayed_verification_id


def test_delayed_verification_checks_exact_lock_and_materialized_content(
    tmp_path: Path,
) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="observed")
    store, manager = _manager(tmp_path, delayed_verification_seconds=0)
    store.put(built.archive_bytes)
    result = _activate(
        manager,
        _plan(built),
        idempotency_key="delayed-observation",
    )

    observations = manager.run_due_delayed_verifications(
        now=datetime.now(timezone.utc),
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation["status"] == "passed"
    assert observation["observed_lock_digest"] == result.workspace_lock.to_dict()[
        "lock_digest"
    ]
    assert observation["receipt"]["components"][0]["package"] == "scenario:recipes"
    assert observation["receipt"]["components"][0]["files"] == 2
    assert not list(manager.pending_observations_root.glob("*.json"))


def test_delayed_verification_records_tamper_without_automatic_rollback(
    tmp_path: Path,
) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="tamper")
    store, manager = _manager(tmp_path, delayed_verification_seconds=0)
    store.put(built.archive_bytes)
    result = _activate(
        manager,
        _plan(built),
        idempotency_key="delayed-tamper",
    )
    target = tmp_path / "workspace" / "scenarios" / "recipes" / "webui.json"
    target.write_text('{"marker":"changed"}\n', encoding="utf-8")

    observation = manager.run_delayed_verification(
        result.operation_id,
        force=True,
    )

    assert observation["status"] == "failed"
    assert "materialized package file" in observation["error"]
    assert manager.load_lock() == result.workspace_lock
    assert json.loads(target.read_text(encoding="utf-8")) == {"marker": "changed"}
    assert not list(manager.pending_observations_root.glob("*.json"))


def test_delayed_verification_marks_moved_lock_as_superseded(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="first-observation")
    second = _built_scenario(tmp_path, version="2.0.0", marker="second-observation")
    store, manager = _manager(tmp_path, delayed_verification_seconds=3600)
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    initial = _activate(manager, _plan(first), idempotency_key="observation-first")
    current = _activate(manager, _plan(second), idempotency_key="observation-second")

    observation = manager.run_delayed_verification(
        initial.operation_id,
        force=True,
    )

    assert observation["status"] == "superseded"
    assert observation["reason"] == "workspace_lock_moved"
    assert observation["observed_lock_digest"] == current.workspace_lock.to_dict()[
        "lock_digest"
    ]
    assert not (
        manager.pending_observations_root
        / f"{initial.delayed_verification_id}.json"
    ).exists()


def test_activation_requires_explicit_reload_and_health_policy(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="policy-required")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)

    with pytest.raises(ActivationError, match="runtime reload requires"):
        manager.activate(_plan(built), idempotency_key="missing-runtime-policy")

    assert manager.load_lock() is None
    assert not (tmp_path / "workspace" / "scenarios" / "recipes").exists()


def test_failed_health_rolls_back_files_and_lock_and_cannot_auto_replay(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="one")
    second = _built_scenario(tmp_path, version="1.1.0", marker="two")
    store, manager = _manager(tmp_path)
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    initial = _activate(manager, _plan(first), idempotency_key="initial")

    with pytest.raises(ActivationError, match="health check failed"):
        _activate(manager,
            _plan(second),
            idempotency_key="upgrade-fails",
            health_check=lambda lock: False,
        )

    target = tmp_path / "workspace" / "scenarios" / "recipes"
    assert json.loads((target / "webui.json").read_text(encoding="utf-8")) == {"marker": "one"}
    assert manager.load_lock() == initial.workspace_lock
    assert not list((tmp_path / "state" / "artifact_pipeline" / "backups").rglob("recipes"))

    with pytest.raises(ActivationReplayBlocked, match="explicitly new idempotency key"):
        _activate(manager, _plan(second), idempotency_key="upgrade-fails")


def test_removed_dependency_is_pruned_from_lock_and_workspace(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="with-skill")
    second = _built_scenario(tmp_path, version="1.1.0", marker="without-skill")
    skill = _built_skill(tmp_path, version="1.0.0", marker="shared")
    store, manager = _manager(tmp_path)
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    store.put(skill.archive_bytes)
    _activate(manager, _plan_with_skill(first, skill), idempotency_key="with-skill")
    skill_target = tmp_path / "workspace" / "skills" / "shopping"
    assert skill_target.is_dir()

    result = _activate(manager, _plan(second), idempotency_key="without-skill")

    assert {item.key for item in result.workspace_lock.components} == {"scenario:recipes"}
    assert result.workspace_lock.bindings == ()
    assert not skill_target.exists()
    operation = json.loads(manager.operation_path(result.operation_id).read_text(encoding="utf-8"))
    assert operation["component_plan"]["removed"] == ["skill:shopping"]


def test_removed_dependency_is_restored_when_post_switch_health_fails(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="rollback-with-skill")
    second = _built_scenario(tmp_path, version="1.1.0", marker="rollback-without-skill")
    skill = _built_skill(tmp_path, version="1.0.0", marker="rollback-shared")
    store, manager = _manager(tmp_path)
    for built in (first, second, skill):
        store.put(built.archive_bytes)
    initial = _activate(manager, _plan_with_skill(first, skill), idempotency_key="rollback-base")

    with pytest.raises(ActivationError, match="health check failed"):
        _activate(manager,
            _plan(second),
            idempotency_key="rollback-remove",
            health_check=lambda _lock: False,
        )

    assert manager.load_lock() == initial.workspace_lock
    assert (tmp_path / "workspace" / "skills" / "shopping" / "handler.py").is_file()


def test_workspace_lock_compare_and_switch_preserves_newer_observed_lock(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="cas-one")
    second = _built_scenario(tmp_path, version="1.1.0", marker="cas-two")
    store, manager = _manager(tmp_path)
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    initial = _activate(manager, _plan(first), idempotency_key="cas-base")
    current = initial.workspace_lock
    foreign = WorkspaceLock(
        lock_revision=current.lock_revision + 1,
        previous_lock_revision=current.lock_revision,
        updated_at="2026-07-26T00:00:00Z",
        slots=current.slots,
        components=current.components,
        bindings=current.bindings,
    )

    def inject_foreign_writer(phase: str) -> None:
        if phase == "switch-lock":
            manager._write_lock(foreign)

    with pytest.raises(ActivationConflictError, match="changed after activation planning"):
        _activate(manager,
            _plan(second),
            idempotency_key="cas-conflict",
            phase_hook=inject_foreign_writer,
        )

    assert manager.load_lock() == foreign
    assert json.loads(
        (tmp_path / "workspace" / "scenarios" / "recipes" / "webui.json").read_text(
            encoding="utf-8"
        )
    ) == {"marker": "cas-one"}


def test_workspace_writer_lease_serializes_two_activations(tmp_path: Path) -> None:
    first = _built_scenario(tmp_path, version="1.0.0", marker="lease-one")
    second = _built_scenario(tmp_path, version="1.1.0", marker="lease-two")
    store, first_manager = _manager(tmp_path)
    second_manager = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=store,
        state_root=tmp_path / "state",
    )
    store.put(first.archive_bytes)
    store.put(second.archive_bytes)
    first_inside = threading.Event()
    release_first = threading.Event()
    errors: list[BaseException] = []

    def hold_first(phase: str) -> None:
        if phase == "fetch":
            first_inside.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("test did not release first activation")

    def run_first() -> None:
        try:
            _activate(first_manager,
                _plan(first),
                idempotency_key="lease-first",
                phase_hook=hold_first,
            )
        except BaseException as exc:  # pragma: no cover - assertion reports the exception
            errors.append(exc)

    def run_second() -> None:
        try:
            _activate(second_manager, _plan(second), idempotency_key="lease-second")
        except BaseException as exc:  # pragma: no cover - assertion reports the exception
            errors.append(exc)

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)
    first_thread.start()
    assert first_inside.wait(timeout=2)
    second_thread.start()
    time.sleep(0.1)
    assert second_thread.is_alive()
    assert not second_manager.operation_path(
        second_manager.operation_id("lease-second")
    ).exists()
    release_first.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    final = second_manager.load_lock()
    assert final is not None
    assert final.lock_revision == 2
    assert final.slots[0].release == "recipes@1.1.0"


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
        _activate(manager,
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

    result = _activate(manager,
        _plan(built),
        idempotency_key="fetch-install",
        fetch_package=lambda ref: fetched.append(ref.digest) or built.archive_bytes,
    )

    assert result.status == "completed"
    assert fetched == [built.ref.digest]
    assert store.has(built.ref.digest)


def test_cached_activation_verifies_and_extracts_each_package_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="single-pass")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)

    import adaos.services.artifact_pipeline.packages as package_module

    original = package_module._verify_artifact_package
    calls: list[str | None] = []

    def counted(data, *, expected_digest=None, limits=None, extract_to=None):
        calls.append(expected_digest)
        return original(
            data,
            expected_digest=expected_digest,
            limits=limits,
            extract_to=extract_to,
        )

    monkeypatch.setattr(package_module, "_verify_artifact_package", counted)

    result = _activate(manager, _plan(built), idempotency_key="single-pass")

    assert result.status == "completed"
    assert calls == [built.ref.digest]
    operation = json.loads(
        manager.operation_path(result.operation_id).read_text(encoding="utf-8")
    )
    verification = operation["package_verification"]
    assert verification["status"] == "completed"
    assert verification["mode"] == "verify_and_extract_once"
    assert verification["packages"][0]["package"] == "scenario:recipes"
    assert verification["packages"][0]["digest"] == built.ref.digest
    assert verification["packages"][0]["file_count"] == 2
    assert verification["packages"][0]["uncompressed_bytes"] > 0


def test_introduced_permissions_require_an_explicit_approval(tmp_path: Path) -> None:
    built = _built_scenario(tmp_path, version="1.0.0", marker="permission")
    store, manager = _manager(tmp_path)
    store.put(built.archive_bytes)
    plan = _plan(built, permissions=("shopping.read",))

    with pytest.raises(ActivationError, match="no explicit permission decision"):
        _activate(manager, plan, idempotency_key="permission-denied-by-default")

    result = _activate(manager,
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
        _activate(manager,
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
    initial = _activate(manager, _plan(first), idempotency_key="migration-initial")
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
        _activate(manager,
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
        _activate(manager,
            _plan(built, migrations=(migration,)),
            idempotency_key="migration-uncertain",
            migration_executor=timeout_after_dispatch,
            migration_rollback=lambda _request: {"status": "rolled_back"},
        )

    assert len(executions) == 1
    with pytest.raises(ActivationReplayBlocked, match="explicitly new idempotency key"):
        _activate(manager,
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
        _activate(manager, _plan(built), idempotency_key="interrupted")

    recovered = manager.recover_interrupted(operation_id)
    assert recovered["status"] == "recovered"
    assert manager.load_lock() is None
