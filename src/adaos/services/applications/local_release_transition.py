"""Bind local cutover to immutable package ownership, never caller-supplied paths."""

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import yaml

from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.artifact_pipeline.trial_activation import (
    legacy_cbs_shared_skill_rebindings,
    load_workspace_lock,
    shared_skill_contract_fingerprint,
)
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
from .data_lifecycle import LocalApplicationDataLifecycle, OwnedDataComponent
from .runtime_channel import ApplicationRuntimeChannel
from .store import ApplicationStore


def _invoke_trial_lifecycle(runtime, lifecycle, hook: str, *, reason: str) -> dict:
    """Invoke one exact immutable Trial lifecycle hook without exposing state."""

    receipts = []
    for component in lifecycle.components:
        hooks = component.target_manifest.get("lifecycle")
        hooks = hooks if isinstance(hooks, dict) else {}
        tool = str(hooks.get(hook) or "").strip()
        if not tool:
            continue
        skill = component.component_ref.split(":", 1)[1]
        manager = runtime.ready_manager(skill)
        result = manager.invoke_active_runtime_lifecycle_hook(
            skill,
            hook_key=hook,
            reason=reason,
            event_type="application.runtime_transition",
            state=f"application_{hook}",
        )
        hook_result = result.get("hook_result") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("ok") is not True
            or result.get("skipped") is True
            or not isinstance(hook_result, dict)
            or hook_result.get("ok") is not True
        ):
            raise ValueError(f"Trial {hook} hook did not return a verified receipt")
        receipts.append(
            {
                "component_ref": component.component_ref,
                "tool": tool,
                "ok": True,
            }
        )
    return {"ok": True, "hook": hook, "components": receipts}


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
            removed_tombstone = bool(installed and installed.status == "removed")
            if removed_tombstone:
                # A reviewed removal is the current local authority.  The
                # workspace lock can still carry its historical slot until a
                # later materialization commit; a new Trial must start from a
                # clean data identity instead of treating that slot as Stable.
                installed = None
            if installed and installed.status != "active":
                raise ValueError("Resolve the in-progress Application installation before data cutover")
            stable_digest = installed.installed_release_digest if installed else None
            workspace_lock_path = workspace / ".adaos/workspace.lock.json"
            workspace_lock = (
                load_workspace_lock(workspace_lock_path)
                if workspace_lock_path.is_file()
                else None
            )
            has_installed_slot = bool(
                workspace_lock
                and any(
                    slot.project_id == application_id
                    for slot in workspace_lock.slots
                )
            )
            if not installed and has_installed_slot and not removed_tombstone:
                raise ValueError(
                    "Reconcile the existing Workspace installation before preparing migrated Beta"
                )
            if stable_digest == runtime.release_digest:
                raise ValueError("Published Candidate has no retained migration binding; explicit recovery required")
            binding = {**identity, "stable_release_digest": stable_digest}
        stable = store.get_release(application_id, stable_digest).project_release if stable_digest else None
        packages = ContentAddressedPackageStore(state / "artifact_pipeline/packages")
        active_lock_path = workspace / ".adaos/workspace.lock.json"
        active_lock = (
            load_workspace_lock(active_lock_path)
            if active_lock_path.is_file()
            else None
        )

        def shared_components(value):
            """Return exact package identities consumed without data ownership."""

            if value is None:
                return {}
            result = {
                item.key: item.package_digest
                for item in getattr(value, "resolved_dependencies", ())
            }
            if value.composition_lock is None:
                return result
            members = {item.ref: item for item in value.composition_lock.members}
            for package in value.components:
                member = members.get(package.key)
                if member is not None and member.lifecycle == "shared":
                    result[package.key] = package.digest
            return result

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
                member = members.get(package.key)
                # Shared dependencies are materialized by the package resolver,
                # but their state authority belongs to another Application.
                # They must therefore remain outside this Application's data
                # transition instead of being treated as an unsupported owner.
                if member is None or member.lifecycle == "shared":
                    continue
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
        legacy_rebindings = {
            str(item.get("skill_ref") or ""): item
            for item in legacy_cbs_shared_skill_rebindings(
                SimpleNamespace(packages=release.components),
                active_lock,
                packages,
            )
        }
        target_shared = shared_components(release)
        relinquished = previous.keys() - target.keys()
        unsafe_relinquished = []
        for ref in sorted(relinquished):
            previous_package = previous[ref][0]
            target_digest = target_shared.get(ref)
            if target_digest == previous_package.digest:
                continue
            try:
                _archive, verified = packages.read_verified(target_digest)
                equivalent = (
                    verified.ref.kind == "skill"
                    and verified.ref.key == ref
                    and shared_skill_contract_fingerprint(
                        previous_package, packages
                    )
                    == shared_skill_contract_fingerprint(verified.ref, packages)
                )
            except Exception:
                equivalent = False
            if not equivalent:
                unsafe_relinquished.append(ref)
        if unsafe_relinquished:
            raise ValueError("Removing owned skill data requires an explicit retention/migration adapter")
        shared_adoptions = {}
        for installation in store.list_installations():
            if installation.application_id == application_id or installation.status == "removed":
                continue
            for item in installation.component_refs:
                ref = item["component_ref"]
                if ref not in target:
                    continue
                if item["lifecycle"] == "bound":
                    raise ValueError("Owned skill is referenced by another installation; reconcile ownership before cutover")
                active_package = next(
                    (
                        package
                        for package in (active_lock.components if active_lock else ())
                        if package.key == ref
                        and package.digest == item["package_digest"]
                    ),
                    None,
                )
                if active_package is None:
                    raise ValueError("Shared skill package is absent from the active Workspace lock")
                try:
                    archive, verified = packages.read_verified(active_package.digest)
                    if verified.ref != active_package:
                        raise ValueError("Shared skill package identity changed")
                    with ZipFile(BytesIO(archive)) as bundle:
                        active_manifest = yaml.safe_load(
                            bundle.read("skill.yaml").decode("utf-8")
                        )
                    if not isinstance(active_manifest, dict):
                        raise ValueError("Shared skill package has no valid manifest")
                    if active_package.digest != target[ref][0].digest:
                        legacy_proof = legacy_rebindings.get(ref)
                        legacy_equivalent = bool(
                            legacy_proof
                            and legacy_proof.get("active_package_digest")
                            == active_package.digest
                            and legacy_proof.get("candidate_package_digest")
                            == target[ref][0].digest
                        )
                        if not legacy_equivalent and (
                            shared_skill_contract_fingerprint(active_package, packages)
                            != shared_skill_contract_fingerprint(target[ref][0], packages)
                        ):
                            raise ValueError(
                                "Shared skill package differs from the proposed owner delivery"
                            )
                except (FileNotFoundError, KeyError, ValueError):
                    raise
                except Exception as exc:
                    raise ValueError(
                        "Shared skill package differs from the proposed owner delivery"
                    ) from exc
                shared_adoptions[ref] = (active_package, active_manifest)
        components = []
        for ref, (package, manifest) in target.items():
            environment = SkillRuntimeEnvironment(skills_root=workspace / "skills", skill_name=package.artifact_id)
            old = previous.get(ref)
            beta_environment = SkillRuntimeEnvironment(skills_root=runtime.root / "skills", skill_name=package.artifact_id)
            adopted_shared = shared_adoptions.get(ref) if old is None else None
            components.append(OwnedDataComponent(ref,
                environment.data_root(old[0].version) if old else (
                    environment.data_root(adopted_shared[0].version)
                    if adopted_shared else None
                ),
                beta_environment.data_root(package.version), environment.data_root(package.version),
                old[1] if old else (adopted_shared[1] if adopted_shared else {}), manifest))
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


def reconcile_rejected_local_trial(owner, candidate_id: str, release_digest: str):
    """Finish local runtime compensation after authoritative Candidate rejection."""
    from dataclasses import replace

    from adaos.domain.artifact_release import ArtifactPackageRef, ProjectRelease, WorkspaceLock
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

    state = Path(owner.paths.state_dir()).resolve()
    workspace = Path(owner.paths.workspace_dir()).resolve()
    activation = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate_id)
    rollback = dict((activation or {}).get("rollback") or {})
    archive_value = str(rollback.get("archive") or "").strip()
    if not activation or activation.get("status") != "detached" or not archive_value:
        return {"ok": True, "status": "not_local_or_not_detached"}
    selections = ApplicationStore(state).list_runtime_selections()
    expected_root = f"trial:{candidate_id}"
    has_exact_selection = any(
        item.runtime_root_ref == expected_root
        and item.release_digest == release_digest
        for item in selections
    )
    root = Path(archive_value).resolve()
    archive_root = (state / "artifact_pipeline/trial-rollbacks").resolve()
    if not has_exact_selection and not root.is_dir():
        # A superseded decision may be reconciled after its retained archive
        # has already expired. With no exact active selection and no archive,
        # there is no local effect left to compensate.
        return {
            "ok": True,
            "status": "superseded_runtime_selection",
            "candidate_id": candidate_id,
            "release_digest": release_digest,
        }
    if not root.is_dir() or not root.is_relative_to(archive_root):
        raise ValueError("Rejected Trial archive is outside the retained recovery root")
    binding = root / ".adaos/data-transition.json"
    if not binding.is_file():
        return {"ok": True, "status": "no_local_data_transition"}
    release_path = root / ".adaos/releases" / f"{release_digest.split(':')[-1]}.json"
    release = ProjectRelease.from_mapping(json.loads(release_path.read_text(encoding="utf-8"))).seal()
    if release.release_digest != release_digest:
        raise ValueError("Rejected Trial release identity changed")
    packages = tuple(ArtifactPackageRef.from_mapping(item) for item in activation.get("package_refs") or [])
    runtime = NativeTrialRuntime(owner, candidate_id, release_digest, root, packages,
                                 project_id=release.project_id)
    lifecycle = bind_local_data_lifecycle(owner, runtime, release)
    original_root = Path(str((activation.get("runtime_binding") or {}).get("path") or "")).resolve()
    expected_trial_root = (workspace.parent / "trials" / candidate_id).resolve()
    if original_root != expected_trial_root:
        raise ValueError("Rejected Trial retained a different original runtime root")
    original_components = tuple(
        replace(component, beta_root=original_root / component.beta_root.relative_to(root))
        for component in lifecycle.components
    )
    lifecycle = LocalApplicationDataLifecycle(
        state_root=lifecycle.state,
        private_root=lifecycle.private,
        application_id=lifecycle.application_id,
        candidate_id=lifecycle.candidate_id,
        release_digest=lifecycle.release_digest,
        stable_digest=lifecycle.stable_digest,
        components=original_components,
    )
    webspace_id = str((activation.get("target") or {}).get("webspace_id") or "").strip()
    if not webspace_id:
        raise ValueError("Rejected Trial has no production Webspace identity")
    metadata = workspace / ".adaos"

    def verify(_key):
        lock = WorkspaceLock.from_mapping(json.loads((metadata / "workspace.lock.json").read_text(encoding="utf-8")))
        slots = [slot for slot in lock.slots if slot.project_id == release.project_id]
        store = ApplicationStore(state)
        if lifecycle.stable_digest:
            installation = store.get_installation(release.project_id)
            if (installation.status != "active"
                    or installation.installed_release_digest != lifecycle.stable_digest
                    or len(slots) != 1 or slots[0].release_digest != lifecycle.stable_digest):
                raise ValueError("Stable code/installation changed during Trial rejection")
        elif slots:
            raise ValueError("Workspace installation appeared during Trial rejection")
        return {"ok": True, "release_digest": lifecycle.stable_digest}

    from .runtime_transition import ApplicationRuntimeTransition

    operation_id = f"application-beta:{release.project_id}:{candidate_id}"
    transition = ApplicationRuntimeTransition(lifecycle.channel).get(operation_id)
    if transition is not None and not transition["completed"]:
        # Preparation can fail before RuntimeSelection ever points at Trial.
        # Candidate rejection archives the immutable runtime, but the retained
        # data journal must still be cancelled against the unchanged Stable
        # source or it will correctly fence every later Candidate.
        result = lifecycle.abort_beta_preparation(
            verify_source=verify,
            source_guard=lambda: mutation_lock(
                metadata / ".workspace-writer.lock", timeout_s=30
            ),
        )
        return {
            "ok": True,
            "status": "aborted_failed_preparation",
            "operation_id": result["operation_id"],
        }

    if not has_exact_selection:
        # Candidate decisions and runtime placement are separate durable
        # authorities. A newer Beta may replace this Candidate before its
        # workflow projection resumes; rejecting the stale Candidate must not
        # roll back that newer selection. An unfinished journal is handled
        # above even though preparation has not selected Trial yet.
        return {
            "ok": True,
            "status": "superseded_runtime_selection",
            "candidate_id": candidate_id,
            "release_digest": release_digest,
        }

    result = lifecycle.reject_beta(
        webspace_id=webspace_id,
        verify_source=verify,
        source_guard=lambda: mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30),
    )
    return {
        "ok": True,
        "status": "rejected",
        "operation_id": result["operation_id"],
        "restored_selections": list(result["intent"]["restored"]),
    }


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

    drained = _invoke_trial_lifecycle(
        runtime,
        lifecycle,
        "drain",
        reason="application_stable_cutover",
    )
    try:
        transition = lifecycle.accept_beta(
            webspace_id=activation["target"]["webspace_id"],
            publish=publish,
        )
    except BaseException:
        _invoke_trial_lifecycle(
            runtime,
            lifecycle,
            "rehydrate",
            reason="application_stable_cutover_failed",
        )
        raise
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return {**result, "data_transition": transition, "drain": drained}
