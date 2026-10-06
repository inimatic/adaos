"""Admit immutable local Trials to the native skill engine, without source fallback."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from adaos.adapters.db.sqlite_store import SQLite, SQLiteKV
from adaos.adapters.fs.path_provider import PathProvider
from adaos.domain.artifact_release import ArtifactPackageRef, ProjectRelease
from adaos.services.agent_context import AgentContext, use_ctx
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.trial_activation import (
    TrialActivationStore, load_workspace_lock, trial_workspace_root,
)


_READY_MANAGER_CACHE_LOCK = threading.Lock()
_READY_MANAGER_BUILD_LOCK = threading.Lock()
_READY_MANAGER_CACHE: dict[
    tuple[str, str, str, str],
    tuple[tuple[tuple[str, int, int, int], ...], object],
] = {}
_READY_MANAGER_CACHE_MAX = 64
_READY_MANAGER_VALIDATED_AT: dict[tuple[str, str, str, str], float] = {}
_READY_MANAGER_REVALIDATE_SECONDS = max(
    0.25,
    min(
        60.0,
        float(os.getenv("ADAOS_TRIAL_MANAGER_REVALIDATE_SECONDS") or "5"),
    ),
)


def _runtime_tree_signature(source: Path, manifest_path: Path) -> tuple[tuple[str, int, int, int], ...]:
    rows: list[tuple[str, int, int, int]] = []
    paths = [manifest_path, *source.rglob("*")]
    for path in paths:
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        rows.append(
            (
                str(path.relative_to(source)) if path.is_relative_to(source) else str(path),
                int(stat.st_size),
                int(stat.st_mtime_ns),
                int(stat.st_ctime_ns),
            )
        )
    rows.sort(key=lambda item: item[0])
    return tuple(rows)


class TrialRuntimeUnavailable(ValueError):
    pass


class TrialPaths(PathProvider):
    def __init__(self, owner: AgentContext, root: Path):
        super().__init__(root / ".runtime")
        self.root = root
        self.runtime_channel_ref = f"trial:{root.name}"
        self.package_dir = Path(owner.paths.package_path())
        self.subnet_id = owner.paths.subnet_id

    def workspace_dir(self) -> Path:
        return self.root

    def dev_dir(self) -> Path:
        raise TrialRuntimeUnavailable("DEV is unavailable in an immutable Trial")


@dataclass(frozen=True)
class NativeTrialRuntime:
    owner: AgentContext
    candidate_id: str
    release_digest: str
    root: Path
    packages: tuple[ArtifactPackageRef, ...]
    project_id: str = ""

    @classmethod
    def resolve(cls, owner: AgentContext, candidate_id: str, release_digest: str) -> "NativeTrialRuntime":
        runtime = cls._resolve_immutable(owner, candidate_id, release_digest)
        state = Path(owner.paths.state_dir())
        record = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate_id)
        from .runtime_channel import ApplicationRuntimeChannel
        from .runtime_transition import ApplicationRuntimeTransition

        application_id = runtime.project_id
        operation_id = f"application-beta:{application_id}:{candidate_id}"
        transition = ApplicationRuntimeTransition(ApplicationRuntimeChannel(state, application_id)).get(operation_id)
        if transition and not transition["completed"]:
            raise TrialRuntimeUnavailable("Trial migration is pending or aborted; it cannot be executed as an empty Trial")
        if record.get("data_mode") == "snapshot":
            proof = record.get("safety_evidence", {}).get("data_transition") or {}
            target = (transition or {}).get("intent", {}).get("target", {})
            if (proof.get("operation_id") != operation_id or not transition or not transition["completed"]
                    or target.get("runtime_root_ref") != f"trial:{candidate_id}"
                    or target.get("release_digest") != release_digest
                    or proof.get("contract_digest") != transition["intent"]["contract_digest"]):
                raise TrialRuntimeUnavailable("Trial snapshot migration is not committed; completion or recovery required")
        elif record.get("data_mode") != "empty":
            raise TrialRuntimeUnavailable("Native Trial data mode is not qualified")
        return runtime

    @classmethod
    def _resolve_immutable(cls, owner: AgentContext, candidate_id: str, release_digest: str) -> "NativeTrialRuntime":
        """Verify Candidate metadata for preparation; this does not admit execution."""
        state = Path(owner.paths.state_dir())
        record = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate_id)
        if not record or record.get("status") not in {"active", "completed"}:
            raise TrialRuntimeUnavailable("Trial is missing or inactive")
        expiry = record.get("expires_at")
        if expiry and datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
            raise TrialRuntimeUnavailable("Trial has expired")
        identity = record.get("candidate_ref") or {}
        binding = record.get("runtime_binding") or {}
        root = trial_workspace_root(Path(owner.paths.workspace_dir()), candidate_id).resolve()
        if (identity.get("candidate_id") != candidate_id or identity.get("release_digest") != release_digest
                or binding.get("authority") != "immutable_candidate"
                or binding.get("kind") != "isolated_trial_workspace"
                or Path(binding.get("path") or "").resolve() != root):
            raise TrialRuntimeUnavailable("Trial binding does not match the exact Candidate")
        lock = load_workspace_lock(root / ".adaos/workspace.lock.json")
        if lock is None or lock.to_dict()["lock_digest"] != binding.get("workspace_lock_digest"):
            raise TrialRuntimeUnavailable("Trial WorkspaceLock is missing or changed")
        packages = tuple(ArtifactPackageRef.from_mapping(item) for item in record.get("package_refs", ()))
        if not packages:
            raise TrialRuntimeUnavailable("Trial has no packages")
        if {item.key: item for item in packages} != {item.key: item for item in lock.components}:
            raise TrialRuntimeUnavailable("Trial package references differ from WorkspaceLock")
        release_path = root / ".adaos/releases" / f"{release_digest.split(':')[-1]}.json"
        release = ProjectRelease.from_mapping(json.loads(release_path.read_text(encoding="utf-8"))).seal()
        if release.release_digest != release_digest or any(item not in packages for item in release.components):
            raise TrialRuntimeUnavailable("Trial release differs from its locked packages")
        return cls(owner, candidate_id, release_digest, root, packages, project_id=release.project_id)

    def component(self, kind: str, name: str) -> ArtifactPackageRef:
        matches = [item for item in self.packages if item.kind == kind and item.artifact_id == name]
        if len(matches) != 1:
            raise TrialRuntimeUnavailable(f"Trial does not own exactly one {kind}:{name}")
        return matches[0]

    def verified_source(self, package: ArtifactPackageRef, *, root: Path | None = None) -> Path:
        store = ContentAddressedPackageStore(Path(self.owner.paths.state_dir()) / "artifact_pipeline/packages")
        archive, verified = store.read_verified(package.digest)
        if verified.ref != package:
            raise TrialRuntimeUnavailable("Trial package identity mismatch")
        source = root or self.root / f"{package.kind}s" / package.artifact_id
        with ZipFile(BytesIO(archive)) as bundle:
            for name in verified.file_names:
                target = source / name
                if not target.resolve().is_relative_to(source.resolve()) or target.is_symlink():
                    raise TrialRuntimeUnavailable("Trial source escaped its package root")
                if not target.is_file() or target.read_bytes() != bundle.read(name):
                    raise TrialRuntimeUnavailable(f"Trial source differs from its package: {name}")
        return source

    def context(self) -> AgentContext:
        paths = TrialPaths(self.owner, self.root)
        sql = SQLite(paths)
        ctx = replace(self.owner, paths=paths, sql=sql, kv=SQLiteKV(sql),
                      settings=self.owner.settings.with_overrides(base_dir=paths.base_dir()),
                      authority_state_dir=self.owner.paths.state_dir(),
                      authority_context=self.owner,
                      relational_storage=None, blob_storage=None, execution_provider=None)
        ctx.config = self.owner.config
        paths.ctx = ctx
        return ctx

    def manager(self, *, prepare: bool = False):
        from adaos.services.skill.manager import SkillManager

        ctx = self.context()
        with use_ctx(ctx):
            manager = SkillManager(git=ctx.git, paths=ctx.paths, repo=ctx.skills_repo,
                                   bus=ctx.bus, caps=ctx.caps, settings=ctx.settings)
            if prepare:
                for package in self.packages:
                    if package.kind != "skill":
                        continue
                    source = self.verified_source(package)
                    manager.prepare_runtime(package.artifact_id, path=source,
                                            version_override=package.version, run_tests=True)
                    manager.activate_runtime(package.artifact_id, version=package.version)
            return manager

    def ready_manager(self, skill: str):
        package = self.component("skill", skill)
        cache_key = (str(self.root), self.release_digest, skill, package.digest)
        with _READY_MANAGER_CACHE_LOCK:
            cached = _READY_MANAGER_CACHE.get(cache_key)
            if cached is not None:
                manager = cached[1]
                setattr(
                    manager,
                    "_adaos_immutable_trial_authority",
                    (str(self.root), self.release_digest, package.digest),
                )
                # Trial workspaces are immutable and were content-verified
                # before entering this cache. A page mounts several read
                # sources concurrently; recursively hashing the complete
                # runtime tree for every waiter turned the integrity check
                # into 5-10 seconds of first-paint latency on Windows. Retain
                # fail-closed periodic revalidation without repeating it for
                # the whole first-paint burst.
                last_validated = float(
                    _READY_MANAGER_VALIDATED_AT.get(cache_key) or 0.0
                )
                if time.monotonic() - last_validated <= _READY_MANAGER_REVALIDATE_SECONDS:
                    return manager
                status = manager.runtime_status(skill)
                manifest_path = Path(status.get("resolved_manifest") or "")
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    source = Path(manifest.get("source") or "").resolve()
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    source = Path()
                if (
                    status.get("ready")
                    and status.get("version") == package.version
                    and source.is_relative_to(self.root / "skills/.runtime")
                    and cached[0] == _runtime_tree_signature(source, manifest_path)
                ):
                    _READY_MANAGER_VALIDATED_AT[cache_key] = time.monotonic()
                    return manager
                # The cached runtime failed immutable-source revalidation.
                # Remove it before entering the single build lane.  The old
                # implementation kept the stale entry, observed it again
                # under ``_READY_MANAGER_BUILD_LOCK`` and recursively called
                # ``ready_manager``.  Because the build lock is deliberately
                # non-reentrant, that path deadlocked the only materialization
                # worker and every later Application navigation appeared to
                # be accepted without ever becoming visible.
                _READY_MANAGER_CACHE.pop(cache_key, None)
                _READY_MANAGER_VALIDATED_AT.pop(cache_key, None)
        with _READY_MANAGER_BUILD_LOCK:
            with _READY_MANAGER_CACHE_LOCK:
                cache_filled = _READY_MANAGER_CACHE.get(cache_key)
                if cache_filled is not None:
                    # Another waiter rebuilt and verified the immutable
                    # runtime while this caller waited for the build lane.
                    # It is fresh by construction; returning it directly also
                    # avoids recursively acquiring the non-reentrant lock.
                    manager = cache_filled[1]
                    setattr(
                        manager,
                        "_adaos_immutable_trial_authority",
                        (str(self.root), self.release_digest, package.digest),
                    )
                    return manager
            manager = self.manager()
            setattr(
                manager,
                "_adaos_immutable_trial_authority",
                (str(self.root), self.release_digest, package.digest),
            )
            status = manager.runtime_status(skill)
            if not status.get("ready") or status.get("version") != package.version:
                raise TrialRuntimeUnavailable("Trial native runtime is not prepared")
            manifest = json.loads(Path(status["resolved_manifest"]).read_text(encoding="utf-8"))
            source = Path(manifest.get("source") or "").resolve()
            if not source.is_relative_to(self.root / "skills/.runtime"):
                raise TrialRuntimeUnavailable("Trial executable source escaped its runtime")
            self.verified_source(package, root=source)
            signature = _runtime_tree_signature(source, Path(status["resolved_manifest"]))
            with _READY_MANAGER_CACHE_LOCK:
                if len(_READY_MANAGER_CACHE) >= _READY_MANAGER_CACHE_MAX:
                    evicted = next(iter(_READY_MANAGER_CACHE))
                    _READY_MANAGER_CACHE.pop(evicted)
                    _READY_MANAGER_VALIDATED_AT.pop(evicted, None)
                _READY_MANAGER_CACHE[cache_key] = (signature, manager)
                _READY_MANAGER_VALIDATED_AT[cache_key] = time.monotonic()
            return manager

    def identity(self, skill: str) -> dict[str, str]:
        package = self.component("skill", skill)
        return {"candidate_id": self.candidate_id, "release_digest": self.release_digest,
                "package_digest": package.digest, "runtime_root": str(self.root)}
