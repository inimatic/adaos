from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

import typer
import yaml

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.project_build import (
    build_workspace_project_release,
    project_release_build_evidence,
)
from adaos.services.workspace_release_guard import load_active_workspace_lock


app = typer.Typer(help="Build and inspect immutable Project releases.")


def _roots(workspace_root: Path | None) -> tuple[Path, Path]:
    ctx = get_ctx()
    workspace = Path(
        workspace_root or ctx.paths.workspace_dir()
    ).expanduser().resolve()
    artifact_root = Path(ctx.paths.state_dir()).resolve() / "artifact_pipeline"
    return workspace, artifact_root


def _git_text(workspace: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _default_revision(workspace: Path) -> str:
    revision = _git_text(workspace, "rev-parse", "HEAD")
    if not revision:
        raise typer.BadParameter("revision is required outside a git checkout", param_hint="--revision")
    return revision


def _default_repository(workspace: Path) -> str:
    repository = _git_text(workspace, "config", "--get", "remote.origin.url")
    if repository:
        return repository
    return workspace.name


def _project_source_paths(workspace: Path, project_id: str) -> tuple[str, ...]:
    manifest = workspace / "projects" / project_id / "project.yaml"
    try:
        project: Any = yaml.safe_load(manifest.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise typer.BadParameter(
            f"cannot read Project manifest: {manifest}",
            param_hint="project_id",
        ) from exc
    owned = (
        project.get("components", {}).get("owned", [])
        if isinstance(project, Mapping)
        and isinstance(project.get("components"), Mapping)
        else []
    )
    paths = [f"projects/{project_id}"]
    for item in owned:
        if not isinstance(item, Mapping):
            continue
        kind, separator, artifact_id = str(item.get("ref") or "").strip().partition(":")
        if separator != ":" or kind not in {"skill", "scenario"} or not artifact_id:
            continue
        plural = "skills" if kind == "skill" else "scenarios"
        paths.append(f"{plural}/{artifact_id}")
    return tuple(dict.fromkeys(paths))


def _assert_project_source_clean(workspace: Path, project_id: str) -> None:
    if not (workspace / ".git").exists():
        return
    paths = _project_source_paths(workspace, project_id)
    dirty = _git_text(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *paths,
    )
    if dirty:
        raise typer.BadParameter(
            "Project source closure has uncommitted changes; checkpoint it before "
            "building an immutable release",
            param_hint="project_id",
        )


def _build_project_release(
    project_id: str,
    *,
    revision: str,
    repository: str,
    forge: str,
    workspace_root: Path | None,
    builder: str,
    lock_workspace_root: Path | None = None,
) -> dict[str, object]:
    workspace, artifact_root = _roots(workspace_root)
    _assert_project_source_clean(workspace, project_id)
    active_workspace_lock = load_active_workspace_lock(
        Path(lock_workspace_root).expanduser().resolve()
        if lock_workspace_root is not None
        else workspace
    )
    result = build_workspace_project_release(
        project_dir=workspace / "projects" / project_id,
        workspace_root=workspace,
        source_ref=ArtifactSourceRef(
            forge=forge,
            repository=repository,
            revision=revision,
            path_scope=(f"projects/{project_id}/",),
        ),
        package_store=ContentAddressedPackageStore(
            artifact_root / "packages"
        ),
        release_repository=ReleaseRepository(
            artifact_root / "release-cache"
        ),
        validation_evidence=(
            project_release_build_evidence(revision, builder=builder),
        ),
        active_workspace_lock=active_workspace_lock,
    )
    return result.to_dict()


def _echo_project_release(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"{payload['project_id']}@{payload['version']} "
        f"{payload['release_digest']} packages={len(payload['packages'])}"
    )


def _publish_project_release(
    payload: dict[str, object],
    *,
    workspace_root: Path | None,
) -> dict[str, object]:
    """Publish a locally verified ProjectRelease and all of its packages."""

    from adaos.services.root.service import RootDeveloperService

    _, artifact_root = _roots(workspace_root)
    project_id = str(payload["project_id"])
    release_digest = str(payload["release_digest"])
    plan = ReleaseRepository(artifact_root / "release-cache").get_release(
        project_id,
        release_digest,
    )
    package_store = ContentAddressedPackageStore(artifact_root / "packages")
    archives = {
        package.digest: package_store.read(package.digest)
        for package in plan.packages
    }

    service = RootDeveloperService()
    remote = service.artifact_release_repository(role="hub")
    remote.put_release(plan, archives)
    return {
        "published": True,
        "project_id": project_id,
        "release_digest": release_digest,
        "packages": len(plan.packages),
    }


@app.command("release-build")
def release_build(
    project_id: str = typer.Argument(
        ..., help="Project id under Workspace projects/."
    ),
    revision: str = typer.Option(
        ..., "--revision", help="Exact immutable source revision."
    ),
    repository: str = typer.Option(
        ..., "--repository", help="Source repository identity."
    ),
    forge: str = typer.Option("github", "--forge"),
    workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    payload = _build_project_release(
        project_id,
        revision=revision,
        repository=repository,
        forge=forge,
        workspace_root=workspace_root,
        builder="adaos.project.release-build",
    )
    _echo_project_release(payload, json_output=json_output)


@app.command("push")
def push(
    project_id: str = typer.Argument(
        ..., help="Project id under Workspace projects/."
    ),
    revision: str | None = typer.Option(
        None, "--revision", help="Exact immutable source revision; defaults to current git HEAD."
    ),
    repository: str | None = typer.Option(
        None, "--repository", help="Source repository identity; defaults to git remote.origin.url."
    ),
    forge: str = typer.Option("github", "--forge"),
    workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    local_only: bool = typer.Option(
        False,
        "--local-only",
        help="Build the immutable release locally without publishing it to Root.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    workspace, _ = _roots(workspace_root)
    payload = _build_project_release(
        project_id,
        revision=revision or _default_revision(workspace),
        repository=repository or _default_repository(workspace),
        forge=forge,
        workspace_root=workspace_root,
        builder="adaos.project.push",
    )
    if not local_only:
        payload["publication"] = _publish_project_release(
            payload,
            workspace_root=workspace_root,
        )
    _echo_project_release(payload, json_output=json_output)


@app.command("release-inspect")
def release_inspect(
    project_id: str,
    release_digest: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    _, artifact_root = _roots(None)
    plan = ReleaseRepository(
        artifact_root / "release-cache"
    ).get_release(project_id, release_digest)
    payload = {
        "schema": "adaos.artifact.release_plan.v1",
        **plan.explain(),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"{plan.release.project_id}@{plan.release.version} "
        f"{plan.release.release_digest} packages={len(plan.packages)}"
    )


__all__ = ["app"]
