"""Bind local cutover to immutable package ownership, never caller-supplied paths."""

from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import yaml

from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
from .data_lifecycle import LocalApplicationDataLifecycle, OwnedDataComponent
from .runtime_channel import ApplicationRuntimeChannel
from .store import ApplicationStore


def bind_local_data_lifecycle(owner, runtime, release):
    """Caller must admit the local publisher before preparing this exact Candidate.

    Retain the original Stable identity before publication can advance the
    installation record. Recovery must not reinterpret the new release as its
    own migration base. Only package manifests enter this plan, not runtime data.
    """
    state = Path(owner.paths.state_dir()).resolve()
    workspace = Path(owner.paths.workspace_dir()).resolve()
    store = ApplicationStore(state)
    application_id = release.project_id
    if release.release_digest != runtime.release_digest:
        raise ValueError("Data transition Candidate identity mismatch")
    binding_path = runtime.root / ".adaos/data-transition.json"
    identity = {"schema": "adaos.application.local_data_binding.v1", "application_id": application_id,
                "candidate_id": runtime.candidate_id, "release_digest": runtime.release_digest}
    with mutation_lock(binding_path.with_suffix(".lock")):
        if binding_path.exists():
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            if any(binding.get(key) != value for key, value in identity.items()):
                raise ValueError("Retained data transition binding changed")
            stable_digest = binding["stable_release_digest"]
        else:
            try:
                installed = store.get_installation(application_id)
            except FileNotFoundError:
                installed = None
            if installed and installed.status != "active":
                raise ValueError("Resolve the in-progress Application installation before data cutover")
            stable_digest = installed.installed_release_digest if installed else None
            if not installed and (workspace / "projects" / application_id / "project.yaml").exists():
                raise ValueError("Reconcile the existing Workspace installation before preparing migrated Beta")
            if stable_digest == runtime.release_digest:
                raise ValueError("Published Candidate has no retained migration binding; explicit recovery required")
            binding = {**identity, "stable_release_digest": stable_digest}
        stable = store.get_release(application_id, stable_digest).project_release if stable_digest else None
        packages = ContentAddressedPackageStore(state / "artifact_pipeline/packages")

        def skills(value):
            if value is None:
                return {}
            if value.composition_lock is None:
                raise ValueError("Data transition requires exact composition ownership")
            members = {item.ref: item for item in value.composition_lock.members}
            result = {}
            for package in value.components:
                if package.kind != "skill":
                    continue
                if members[package.key].lifecycle != "bound":
                    raise ValueError("Shared skills require a qualified shared-data cutover adapter")
                archive, verified = packages.read_verified(package.digest)
                if verified.ref != package:
                    raise ValueError("Data transition package identity mismatch")
                with ZipFile(BytesIO(archive)) as bundle:
                    manifest = yaml.safe_load(bundle.read("skill.yaml").decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("Data transition requires a skill manifest")
                result[package.key] = (package, manifest)
            return result

        previous, target = skills(stable), skills(release)
        if previous.keys() - target.keys():
            raise ValueError("Removing owned skill data requires an explicit retention/migration adapter")
        for installation in store.list_installations():
            if installation.application_id == application_id or installation.status == "removed":
                continue
            if any(item["component_ref"] in target for item in installation.component_refs):
                raise ValueError("Owned skill is referenced by another installation; reconcile ownership before cutover")
        components = []
        for ref, (package, manifest) in target.items():
            environment = SkillRuntimeEnvironment(skills_root=workspace / "skills", skill_name=package.artifact_id)
            old = previous.get(ref)
            beta_environment = SkillRuntimeEnvironment(skills_root=runtime.root / "skills", skill_name=package.artifact_id)
            components.append(OwnedDataComponent(ref,
                environment.data_root(old[0].version) if old else None,
                beta_environment.data_root(package.version), environment.data_root(package.version),
                old[1] if old else {}, manifest))
        lifecycle = LocalApplicationDataLifecycle(state_root=state, private_root=workspace.parent,
            application_id=application_id, candidate_id=runtime.candidate_id,
            release_digest=release.release_digest, stable_digest=stable_digest, components=tuple(components))
        if not binding_path.exists():
            atomic_write_json(binding_path, binding)
        # Adopt legacy selections without changing their channel or revision.
        channel = ApplicationRuntimeChannel(state, application_id)
        if channel.read() is None:
            legacy = tuple(item for item in store.list_runtime_selections() if item.application_id == application_id)
            with channel._connection() as connection:
                channel._initialize(connection, legacy)
        return lifecycle


def promote_with_local_data(owner, candidate_id, promote):
    """Run native publication inside an admitted local Beta data transaction.

    Called by the Root service so Builder Web, chat, SDK and CLI share the same
    boundary. Legacy/unplaced Candidates retain their existing publication path;
    a selected legacy Trial with mutable data cannot silently discard that data.
    """
    from adaos.domain.artifact_release import ProjectRelease, WorkspaceLock
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore, trial_workspace_root
    from adaos.services.policy.skill_capabilities import require_skill_capability
    from .service import ApplicationService
    from .trial_runtime import NativeTrialRuntime

    state = Path(owner.paths.state_dir()).resolve()
    workspace = Path(owner.paths.workspace_dir()).resolve()
    activation = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate_id)
    root = trial_workspace_root(workspace, candidate_id).resolve()
    binding = root / ".adaos/data-transition.json"
    if not binding.exists():
        if activation:
            store = ApplicationStore(state)
            selected = any(item.runtime_root_ref == f"trial:{candidate_id}" for item in store.list_runtime_selections())
            if selected and any(path.is_file() for path in (root / "skills/.runtime").glob("*/v*/data/**/*")):
                raise ValueError("Selected legacy Trial data has no migration evidence; explicit data recovery required")
        return promote()
    if not activation or activation.get("status") != "completed":
        raise ValueError("Local Beta data adoption requires the exact accepted Candidate")
    runtime = NativeTrialRuntime.resolve(owner, candidate_id, activation["candidate_ref"]["release_digest"])
    release_path = root / ".adaos/releases" / (runtime.release_digest.split(":")[1] + ".json")
    release = ProjectRelease.from_mapping(json.loads(release_path.read_text(encoding="utf-8"))).seal()
    service = ApplicationService(ApplicationStore(state))
    application = service.store.get_application(release.project_id)
    config = owner.config
    subnet = str(config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id)
    subnet = subnet if subnet.startswith("subnet:") else f"subnet:{subnet}"
    if application.publisher_ref.lower() != subnet.lower():
        raise ValueError("Only the local publisher may adopt local Beta data")
    if getattr(owner.skill_ctx.get(), "name", None):
        require_skill_capability(owner, "applications.publish")
    lifecycle = bind_local_data_lifecycle(owner, runtime, release)
    result_path = lifecycle.recovery / "publication-result.json"

    def publish(_key):
        result = promote()
        if not result.get("ok") or result.get("error") or result.get("status") == "stale":
            raise ValueError("Native publication is not verified; data and runtime remain fenced")
        metadata_root = workspace / ".adaos"
        with mutation_lock(metadata_root / ".workspace-writer.lock", timeout_s=30):
            lock = WorkspaceLock.from_mapping(json.loads((metadata_root / "workspace.lock.json").read_text(encoding="utf-8")))
            installation = service.reconcile_workspace_installation(application.application_id, runtime.release_digest, lock)
        atomic_write_json(result_path, result)
        return {"ok": True, "release_digest": runtime.release_digest, "installation_revision": installation.revision}

    transition = lifecycle.accept_beta(webspace_id=activation["target"]["webspace_id"], publish=publish)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return {**result, "data_transition": transition}
