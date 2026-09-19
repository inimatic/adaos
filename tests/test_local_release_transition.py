from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import yaml

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactSourceRef, ProjectRelease, ProjectCompositionLock, ProjectMemberLock, WorkspaceLock, WorkspaceSlot
from adaos.services.applications.data_lifecycle import declared_databases
from adaos.services.applications.local_release_transition import bind_local_data_lifecycle, promote_with_local_data
from adaos.services.applications.service import ApplicationService
from adaos.services.applications.store import ApplicationStore
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable
from adaos.services.artifact_pipeline.packages import build_artifact_package, ContentAddressedPackageStore
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore, trial_workspace_root


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT id, label FROM entries ORDER BY id").fetchall()


def test_unmanaged_project_source_is_not_an_installed_migration_base(
    monkeypatch, tmp_path: Path
) -> None:
    from adaos.services.applications import local_release_transition

    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    (workspace / "projects/sample").mkdir(parents=True)
    (workspace / "projects/sample/project.yaml").write_text(
        "schema: adaos.project.v1\nid: sample\n", encoding="utf-8"
    )
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-09-19T00:00:00Z",
        components=(),
        slots=(),
    )
    atomic_write_json(workspace / ".adaos/workspace.lock.json", lock.to_dict())
    owner = SimpleNamespace(
        paths=SimpleNamespace(
            state_dir=lambda: state,
            workspace_dir=lambda: workspace,
        )
    )
    runtime = SimpleNamespace(
        root=tmp_path / "trial",
        candidate_id="candidate-sample",
        release_digest="sha256:" + "a" * 64,
    )
    runtime.root.mkdir()
    release = SimpleNamespace(
        project_id="sample",
        release_digest=runtime.release_digest,
        composition_lock=SimpleNamespace(members=()),
        components=(),
    )

    class Store:
        def get_installation(self, _application_id):
            raise FileNotFoundError

        def list_installations(self):
            return []

        def list_runtime_selections(self):
            return []

    monkeypatch.setattr(local_release_transition, "ApplicationStore", lambda _state: Store())
    monkeypatch.setattr(
        local_release_transition,
        "ApplicationRuntimeChannel",
        lambda *_args: SimpleNamespace(read=lambda: {"source": "legacy"}),
    )

    lifecycle = bind_local_data_lifecycle(owner, runtime, release)

    assert lifecycle.stable_digest is None
    binding = json.loads(
        (runtime.root / ".adaos/data-transition.json").read_text(encoding="utf-8")
    )
    assert binding["stable_release_digest"] is None


def test_workspace_slot_without_installation_requires_reconciliation(
    monkeypatch, tmp_path: Path
) -> None:
    from adaos.services.applications import local_release_transition

    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    slot = WorkspaceSlot(
        slot_id="sample",
        project_id="sample",
        release="sample@0.1.0",
        release_digest="sha256:" + "b" * 64,
    )
    atomic_write_json(
        workspace / ".adaos/workspace.lock.json",
        WorkspaceLock(
            lock_revision=1,
            updated_at="2026-09-19T00:00:00Z",
            components=(),
            slots=(slot,),
        ).to_dict(),
    )
    owner = SimpleNamespace(
        paths=SimpleNamespace(
            state_dir=lambda: state,
            workspace_dir=lambda: workspace,
        )
    )
    runtime = SimpleNamespace(
        root=tmp_path / "trial",
        candidate_id="candidate-sample",
        release_digest="sha256:" + "a" * 64,
    )
    runtime.root.mkdir()
    release = SimpleNamespace(
        project_id="sample",
        release_digest=runtime.release_digest,
    )

    class Store:
        def get_installation(self, _application_id):
            raise FileNotFoundError

    monkeypatch.setattr(local_release_transition, "ApplicationStore", lambda _state: Store())

    with pytest.raises(ValueError, match="Reconcile the existing Workspace installation"):
        bind_local_data_lifecycle(owner, runtime, release)


@pytest.fixture
def setup(tmp_path):
    state, workspace = tmp_path / "state", tmp_path / "workspace"
    owner = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: state, workspace_dir=lambda: workspace),
                            config=SimpleNamespace(subnet_id="home"), skill_ctx=SimpleNamespace(get=lambda: None))
    service = ApplicationService(ApplicationStore(state))
    service.register(Application(application_id="sample", legacy_project_id="sample", slug="sample",
        publisher_ref="subnet:home", display={"title": "Sample", "summary": None}, visibility="private",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:sample"},),
        publisher={"publisher_ref": "subnet:home", "display_name": "Home", "subnet_short_ref": "home",
                   "release_key_ref": "key:home", "release_key_fingerprint": "sha256:" + "a" * 64,
                   "home_zone": "local", "trust_relation": "local"}))
    packages = ContentAddressedPackageStore(state / "artifact_pipeline/packages")
    source = ArtifactSourceRef(forge="github", repository="test/sample", revision="a" * 40, path_scope=("skills/worker/",))
    releases = []
    for version in ("0.1.0", "0.2.0"):
        root = tmp_path / "sources" / version
        root.mkdir(parents=True)
        manifest = {"name": "worker", "version": version, "data_lifecycle": {
            "schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools", "databases": [{"path": "entries.sqlite",
            "migrations": [{"version": 1, "name": "initial", "statements": ["CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, label TEXT)"]}]}]}}
        (root / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        built = build_artifact_package(root, kind="skill", source_ref=source)
        package = packages.put(built.archive_bytes, expected_digest=built.ref.digest).ref
        composition = ProjectCompositionLock(project_definition_digest="sha256:" + "b" * 64, profiles=(),
            members=(ProjectMemberLock(ref=package.key, package_digest=package.digest, role="primary",
                exposure="application", lifecycle="bound", relations=("realizes",)),),
            project_dependencies=(), entrypoints=(), compatibility={}, lifecycle={})
        release = ProjectRelease(project_id="sample", version=version, source_ref=source, components=(package,), composition_lock=composition).seal()
        service.register_release(ApplicationRelease(application_id="sample", publisher_ref="subnet:home", project_release=release,
            accepted_candidate_id="candidate-sample", acceptance_evidence=({"status": "passed"},), provenance_refs=(release.release_digest,), lifecycle="trial"))
        releases.append(release)
    old, new = releases

    def lock(release):
        return WorkspaceLock(lock_revision=1, updated_at="2026-09-15T00:00:00Z", components=release.components,
            slots=(WorkspaceSlot(slot_id="sample", project_id="sample", release="sample@" + release.version, release_digest=release.release_digest),))

    atomic_write_json(workspace / ".adaos/workspace.lock.json", lock(old).to_dict())
    service.reconcile_workspace_installation("sample", old.release_digest, lock(old))
    service.select_runtime(webspace_id="desktop", application_id="sample", source="stable_installation",
        release_digest=old.release_digest, runtime_root_ref="workspace", expected_revision=0,
        actor_ref="test", subnet_ref="subnet:home", capability="applications.apply")
    data = workspace / "skills/.runtime/worker/v0.1/data/entries.sqlite"
    data.parent.mkdir(parents=True)
    with closing(sqlite3.connect(data)) as connection:
        connection.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, label TEXT)")
        connection.execute("INSERT INTO entries VALUES(1, 'private stable')")
        connection.commit()
    trial = trial_workspace_root(workspace, "candidate-sample").resolve()
    atomic_write_json(trial / ".adaos/workspace.lock.json", lock(new).to_dict())
    atomic_write_json(trial / ".adaos/releases" / (new.release_digest.split(":")[1] + ".json"), new.to_dict())
    packages.materialize(new.components[0].digest, trial / "skills/worker")
    activations = TrialActivationStore(state / "artifact_pipeline/trial-activations")
    activations.save({"schema": "adaos.trial.activation.v1", "status": "active", "data_mode": "empty",
        "candidate_ref": {"candidate_id": "candidate-sample", "release_digest": new.release_digest},
        "release_ref": {"project_id": "sample", "digest": new.release_digest},
        "target": {"webspace_id": "desktop", "space_kind": "workspace"},
        "runtime_binding": {"authority": "immutable_candidate", "kind": "isolated_trial_workspace",
            "path": str(trial), "workspace_lock_digest": lock(new).to_dict()["lock_digest"]},
        "package_refs": [item.to_dict() for item in new.components]})
    return owner, service, old, new, activations, lock


def test_installed_builder_beta_switches_and_root_publication_adopts_data(setup, monkeypatch):
    from adaos.sdk.builder import applications, workflow
    from adaos.sdk.developer import projects
    from adaos.services.component_updates import ComponentUpdateService
    from adaos.services.workspaces.relations import WebspaceRelationshipRegistry

    owner, service, old, new, activations, lock = setup
    monkeypatch.setattr(applications, "_ctx", lambda: owner)
    monkeypatch.setattr(applications, "publisher_context", lambda: {"publisher_ref": "subnet:home"})
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:home")
    monkeypatch.setattr(workflow, "get_state", lambda *_: {"delivery": {
        "candidate_id": "candidate-sample", "package_digest": new.components[0].digest,
        "release_digest": new.release_digest, "status": "trial"}})
    monkeypatch.setattr(projects, "get_candidate", lambda _: {"candidate": {"release_digest": new.release_digest, "validation_evidence": [{"status": "passed"}]}})
    monkeypatch.setattr(WebspaceRelationshipRegistry, "from_context", lambda: SimpleNamespace(resolve_production_host=lambda value: value))
    monkeypatch.setattr(NativeTrialRuntime, "ready_manager", lambda *_: object())
    updates = ComponentUpdateService(owner.paths.state_dir())
    updates.record_aprobation(component_type="scenario", component_id="sample", aprobation={
        "source_kind": "builder_local_trial", "trial": {"candidate_id": "previous-candidate",
        "candidate_digest": old.components[0].digest, "release_digest": old.release_digest,
        "version": old.version, "status": "published"}})
    refreshes = []
    def refresh(value):
        notice = ComponentUpdateService(owner.paths.state_dir()).current_component_metadata("scenario", "sample")
        assert notice["stage"] == "beta"
        assert notice["candidate"]["id"] == "candidate-sample"
        assert notice["candidate"]["release_digest"] == new.release_digest
        refreshes.append(value)
        return {"ok": True}
    monkeypatch.setattr(applications, "refresh_placement", refresh)
    result = applications.place_local_trial("candidate-sample", webspace_id="desktop", actor_ref="user:owner")
    assert result["ok"] and result["data_transition"]["completed"]
    assert service.list_models()[0]["use_prerelease"]
    assert not service.list_models()[0]["prerelease_following"]
    notices = updates._read()
    applications.place_local_trial("candidate-sample", webspace_id="desktop", actor_ref="user:owner")
    assert updates._read() == notices
    runtime = NativeTrialRuntime.resolve(owner, "candidate-sample", new.release_digest)
    binding = bind_local_data_lifecycle(owner, runtime, new)
    assert rows(binding.components[0].beta_root / "entries.sqlite") == [(1, "private stable")]
    with closing(sqlite3.connect(binding.components[0].beta_root / "entries.sqlite")) as connection:
        connection.execute("INSERT INTO entries VALUES(2, 'private beta')")
        connection.commit()
    activations.update("candidate-sample", status="completed")
    calls = []

    def publish():
        calls.append(1)
        atomic_write_json(owner.paths.workspace_dir() / ".adaos/workspace.lock.json", lock(new).to_dict())
        return {"ok": True, "status": "published", "release_digest": new.release_digest}

    assert promote_with_local_data(owner, "candidate-sample", publish)["data_transition"]["completed"]
    assert rows(binding.components[0].target_root / "entries.sqlite") == [(1, "private stable"), (2, "private beta")]
    assert service.store.get_runtime_selection("desktop", "sample").runtime_root_ref == "workspace"
    assert not service.list_models()[0]["use_prerelease"]
    assert bind_local_data_lifecycle(owner, runtime, new).stable_digest == old.release_digest
    assert promote_with_local_data(owner, "candidate-sample", publish)["ok"]
    assert len(calls) == 1 and refreshes == ["desktop", "desktop"]
    private_metadata = (runtime.root / ".adaos/data-transition.json").read_text(encoding="utf-8")
    assert "private stable" not in private_metadata and "private beta" not in private_metadata


def test_snapshot_label_without_completed_journal_does_not_admit_runtime(setup):
    owner, _, _, new, activations, _ = setup
    activations.update("candidate-sample", data_mode="snapshot")
    with pytest.raises(TrialRuntimeUnavailable, match="not committed"):
        NativeTrialRuntime.resolve(owner, "candidate-sample", new.release_digest)


def test_binding_uses_package_manifest_not_mutable_source(setup):
    owner, _, _, new, _, _ = setup
    runtime = NativeTrialRuntime.resolve(owner, "candidate-sample", new.release_digest)
    (runtime.root / "skills/worker/skill.yaml").write_text("name: impostor", encoding="utf-8")
    lifecycle = bind_local_data_lifecycle(owner, runtime, new)
    assert set(declared_databases(lifecycle.components[0].target_manifest)) == {"entries.sqlite"}
    with pytest.raises(TrialRuntimeUnavailable, match="differs from its package"):
        runtime.verified_source(new.components[0])


def test_foreign_publisher_cannot_place_local_beta(setup, monkeypatch):
    from adaos.sdk.builder import applications
    owner, _, _, _, _, _ = setup
    owner.config.subnet_id = "guest"
    monkeypatch.setattr(applications, "_ctx", lambda: owner)
    with pytest.raises(ValueError, match="local Application publisher"):
        applications._admit_builder_mutation("create_trial", "sample", subnet_ref="subnet:guest", capability="applications.develop")


def test_sdk_recovery_verifies_stable_identity_before_unfencing(setup, monkeypatch):
    from adaos.sdk.builder import applications
    from adaos.services.component_updates import ComponentUpdateService
    from adaos.services.applications.runtime_channel import RuntimeChannelConflict

    owner, service, old, new, _activations, lock = setup
    runtime = NativeTrialRuntime._resolve_immutable(owner, "candidate-sample", new.release_digest)
    lifecycle = bind_local_data_lifecycle(owner, runtime, new)
    with pytest.raises(RuntimeChannelConflict):
        lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": False})
    monkeypatch.setattr(applications, "_ctx", lambda: owner)
    monkeypatch.setattr(applications, "refresh_placement", lambda _: {"ok": True})
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials", lambda *_, **__: 0)
    atomic_write_json(owner.paths.workspace_dir() / ".adaos/workspace.lock.json", lock(new).to_dict())
    with pytest.raises(ValueError, match="Stable code/installation changed"):
        applications.abort_local_trial_preparation("candidate-sample", release_digest=new.release_digest, actor_ref="user:owner")
    with pytest.raises(RuntimeChannelConflict, match="fenced"):
        with lifecycle.channel.execution("workspace", old.release_digest):
            pass
    atomic_write_json(owner.paths.workspace_dir() / ".adaos/workspace.lock.json", lock(old).to_dict())
    result = applications.abort_local_trial_preparation("candidate-sample", release_digest=new.release_digest, actor_ref="user:owner")
    assert result["status"] == "aborted"
    assert service.store.get_runtime_selection("desktop", "sample").release_digest == old.release_digest
    with lifecycle.channel.execution("workspace", old.release_digest):
        pass
    with pytest.raises(TrialRuntimeUnavailable, match="pending or aborted"):
        NativeTrialRuntime.resolve(owner, "candidate-sample", new.release_digest)
