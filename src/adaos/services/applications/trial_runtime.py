"""Admit immutable local Trials to the native skill engine, without source fallback."""

from __future__ import annotations

import json
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


class TrialRuntimeUnavailable(ValueError):
    pass


class TrialPaths(PathProvider):
    def __init__(self, owner: AgentContext, root: Path):
        super().__init__(root / ".runtime")
        self.root = root
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

    @classmethod
    def resolve(cls, owner: AgentContext, candidate_id: str, release_digest: str) -> "NativeTrialRuntime":
        state = Path(owner.paths.state_dir())
        record = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate_id)
        if not record or record.get("status") != "active":
            raise TrialRuntimeUnavailable("Trial is missing or inactive")
        if record.get("data_mode") != "empty":
            raise TrialRuntimeUnavailable("Native Trial execution currently supports empty isolated data only")
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
        return cls(owner, candidate_id, release_digest, root, packages)

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
        manager = self.manager()
        status = manager.runtime_status(skill)
        if not status.get("ready") or status.get("version") != package.version:
            raise TrialRuntimeUnavailable("Trial native runtime is not prepared")
        manifest = json.loads(Path(status["resolved_manifest"]).read_text(encoding="utf-8"))
        source = Path(manifest.get("source") or "").resolve()
        if not source.is_relative_to(self.root / "skills/.runtime"):
            raise TrialRuntimeUnavailable("Trial executable source escaped its runtime")
        self.verified_source(package, root=source)
        return manager

    def identity(self, skill: str) -> dict[str, str]:
        package = self.component("skill", skill)
        return {"candidate_id": self.candidate_id, "release_digest": self.release_digest,
                "package_digest": package.digest, "runtime_root": str(self.root)}
