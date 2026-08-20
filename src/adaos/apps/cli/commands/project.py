from __future__ import annotations

import json
from pathlib import Path

import typer

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.project_build import (
    build_workspace_project_release,
)


app = typer.Typer(help="Build and inspect immutable Project releases.")


def _roots(workspace_root: Path | None) -> tuple[Path, Path]:
    ctx = get_ctx()
    workspace = Path(
        workspace_root or ctx.paths.workspace_dir()
    ).expanduser().resolve()
    artifact_root = Path(ctx.paths.state_dir()).resolve() / "artifact_pipeline"
    return workspace, artifact_root


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
    workspace, artifact_root = _roots(workspace_root)
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
            {
                "schema": "adaos.artifact.project_release_build_evidence.v1",
                "source_revision": revision,
                "builder": "adaos.project.release-build",
            },
        ),
    )
    payload = result.to_dict()
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"{payload['project_id']}@{payload['version']} "
        f"{payload['release_digest']} packages={len(payload['packages'])}"
    )


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
