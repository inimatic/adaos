from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from adaos.domain.artifact_release import ArtifactSourceRef


class SourceProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedSource:
    source_ref: ArtifactSourceRef
    path: Path
    tree_revision: str


class SourceProvider(Protocol):
    def resolve(
        self,
        repository: str,
        revision: str,
        *,
        path_scope: Sequence[str] = (),
    ) -> ArtifactSourceRef: ...

    def materialize(self, source_ref: ArtifactSourceRef, target: Path) -> MaterializedSource: ...

    def tree_revision(self, source_ref: ArtifactSourceRef) -> str: ...


class LocalGitSourceProvider:
    """Forge-independent exact-revision provider backed by local Git mirrors."""

    def __init__(
        self,
        repositories: Mapping[str, Path],
        *,
        forge: str = "git",
        git_executable: str = "git",
    ) -> None:
        self.repositories = {
            str(key): Path(value).expanduser().resolve() for key, value in repositories.items()
        }
        self.forge = forge
        self.git_executable = git_executable

    def _repository(self, repository: str) -> Path:
        try:
            path = self.repositories[repository]
        except KeyError as exc:
            raise SourceProviderError(f"unknown source repository: {repository}") from exc
        if not (path / ".git").exists() and not (path / "HEAD").exists():
            raise SourceProviderError(f"source repository is not a Git repository: {path}")
        return path

    def _git(self, repository: Path, *args: str) -> str:
        command = [self.git_executable, "-C", str(repository), *args]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = ""
            if isinstance(exc, subprocess.CalledProcessError):
                detail = str(exc.stderr or exc.stdout or "").strip()
            raise SourceProviderError(
                f"Git source operation failed ({' '.join(args)}): {detail or exc}"
            ) from exc
        return result.stdout.strip()

    def resolve(
        self,
        repository: str,
        revision: str,
        *,
        path_scope: Sequence[str] = (),
    ) -> ArtifactSourceRef:
        repo = self._repository(repository)
        exact = self._git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
        return ArtifactSourceRef(
            forge=self.forge,
            repository=repository,
            revision=exact,
            path_scope=tuple(path_scope),
        )

    def tree_revision(self, source_ref: ArtifactSourceRef) -> str:
        repo = self._repository(source_ref.repository)
        exact = self._git(repo, "rev-parse", "--verify", f"{source_ref.revision}^{{tree}}")
        return exact

    def materialize(self, source_ref: ArtifactSourceRef, target: Path) -> MaterializedSource:
        if source_ref.forge != self.forge:
            raise SourceProviderError(
                f"source forge {source_ref.forge!r} is not handled by {self.forge!r} provider"
            )
        repo = self._repository(source_ref.repository)
        resolved = self.resolve(
            source_ref.repository,
            source_ref.revision,
            path_scope=source_ref.path_scope,
        )
        if resolved.revision != source_ref.revision:
            raise SourceProviderError("SourceRef revision is not the resolved immutable commit")
        target = Path(target).expanduser().resolve()
        if target.exists():
            raise FileExistsError(f"source target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._git(repo, "worktree", "add", "--detach", str(target), source_ref.revision)
            if source_ref.path_scope:
                self._git(target, "sparse-checkout", "init", "--no-cone")
                self._git(
                    target,
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    *source_ref.path_scope,
                )
            head = self._git(target, "rev-parse", "HEAD")
            if head != source_ref.revision:
                raise SourceProviderError(
                    f"materialized source HEAD differs from SourceRef: {head} != {source_ref.revision}"
                )
            return MaterializedSource(
                source_ref=source_ref,
                path=target,
                tree_revision=self.tree_revision(source_ref),
            )
        except Exception:
            try:
                self._git(repo, "worktree", "remove", "--force", str(target))
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
            raise

    def remove(self, source_ref: ArtifactSourceRef, target: Path) -> None:
        repo = self._repository(source_ref.repository)
        target = Path(target).expanduser().resolve()
        self._git(repo, "worktree", "remove", "--force", str(target))


__all__ = [
    "LocalGitSourceProvider",
    "MaterializedSource",
    "SourceProvider",
    "SourceProviderError",
]
