from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import typer

from adaos.apps.cli.commands import project as project_cli
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.project_build import project_source_snapshot
from adaos.services.builder.workspace import (
    BuilderSourceRecoveryRequired,
    BuilderWorkspaceService,
)
from adaos.services.semver import bump_version
from adaos.sdk.developer import compositions


app = typer.Typer(help="Manage complete Projects in the local DEV workspace.")


def _service() -> BuilderWorkspaceService:
    return BuilderWorkspaceService.from_context()


def _dev_workspace_root(service: BuilderWorkspaceService) -> Path:
    return service._dev_projects_root().parent


def _echo(payload: Any, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, Mapping):
        project_id = str(payload.get("project_id") or payload.get("id") or "")
        status = str(payload.get("status") or "")
        version = str(payload.get("version") or "")
        suffix = " ".join(item for item in (version, status) if item)
        typer.echo(f"{project_id}{' ' + suffix if suffix else ''}")
        return
    for item in payload:
        project_id = str(item.get("id") or item.get("project_id") or "")
        version = str(item.get("version") or "")
        stage = str(item.get("stage") or "")
        typer.echo(" ".join(value for value in (project_id, version, stage) if value))


def _project(project_id: str) -> dict[str, Any]:
    try:
        return compositions.get(project_id)
    except Exception as exc:
        raise typer.BadParameter(str(exc), param_hint="project_id") from exc


def _snapshot(service: BuilderWorkspaceService, project_id: str) -> dict[str, Any]:
    root = _dev_workspace_root(service)
    return project_source_snapshot(
        project_dir=root / "projects" / project_id,
        workspace_root=root,
    )


def _list_project_records(
    service: BuilderWorkspaceService,
    *,
    profile: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    maximum = max(1, min(int(limit), 5000))
    root = service._dev_projects_root()
    if not root.is_dir():
        return result
    for manifest in sorted(root.glob("*/project.yaml"), key=lambda item: item.parent.name.lower()):
        try:
            project = compositions.get(manifest.parent.name)
        except Exception as exc:
            result.append(
                {
                    "id": manifest.parent.name,
                    "status": "invalid",
                    "source_path": str(manifest.parent.resolve()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            if profile and profile not in set(project.get("profiles") or []):
                continue
            result.append({**project, "status": "ready"})
        if len(result) >= maximum:
            break
    return result


@app.command("list")
def list_projects(
    profile: str | None = typer.Option(None, "--profile"),
    limit: int = typer.Option(500, "--limit", min=1, max=5000),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    projects = _list_project_records(
        _service(),
        profile=profile,
        limit=limit,
    )
    _echo(projects, json_output=json_output)


@app.command("show")
def show(
    project_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    _echo(_project(project_id), json_output=json_output)


@app.command("status")
def status(
    project_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    service = _service()
    project = _project(project_id)
    development = service.development_source_status(
        kind="project",
        artifact_id=project_id,
    )
    source = _snapshot(service, project_id)
    relation = "dev_only"
    workspace_source: dict[str, Any] | None = None
    workspace_project = service._workspace_project_root(project_id)
    if workspace_project is not None and service.workspace_root is not None:
        try:
            workspace_source = project_source_snapshot(
                project_dir=workspace_project,
                workspace_root=Path(service.workspace_root),
            )
            relation = (
                "unchanged"
                if workspace_source["source_revision"] == source["source_revision"]
                else "modified"
            )
        except Exception as exc:
            relation = "workspace_unresolved"
            workspace_source = {"error": f"{type(exc).__name__}: {exc}"}
    payload = {
        "schema": "adaos.dev.project_status.v1",
        "project_id": project_id,
        "version": project.get("version"),
        "stage": str((project.get("publication") or {}).get("stage") or "alpha"),
        "status": development.get("status"),
        "workspace_relation": relation,
        "source_revision": source["source_revision"],
        "source": source,
        "workspace_source": workspace_source,
        "development_source": development,
    }
    _echo(payload, json_output=json_output)


@app.command("materialize")
def materialize(
    project_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    service = _service()
    try:
        payload = service.materialize_dev_source(
            kind="project",
            artifact_id=project_id,
        )
    except BuilderSourceRecoveryRequired as exc:
        payload = {
            "ok": False,
            "status": "source_recovery_required",
            "project_id": project_id,
            "source_recovery_plan": exc.plan,
        }
        if json_output:
            _echo(payload, json_output=True)
            raise typer.Exit(2)
        raise typer.BadParameter(
            "source recovery requires explicit per-component decisions",
            param_hint="project_id",
        ) from exc
    _echo(payload, json_output=json_output)


@app.command("push")
def push(
    project_id: str,
    bump: str = typer.Option(
        "patch",
        "--bump",
        help="Project version bump: patch, minor, major, or none.",
    ),
    repository: str | None = typer.Option(
        None,
        "--repository",
        help="Content-addressed DEV source repository identity.",
    ),
    local_only: bool = typer.Option(
        False,
        "--local-only",
        help="Build and retain the immutable ProjectRelease locally.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    service = _service()
    project = _project(project_id)
    bump_token = str(bump or "").strip().lower()
    bump_indexes = {"major": 0, "minor": 1, "patch": 2}
    if bump_token not in {*bump_indexes, "none"}:
        raise typer.BadParameter(
            "bump must be patch, minor, major, or none",
            param_hint="--bump",
        )
    manifest_path: Path | None = None
    original_manifest: bytes | None = None
    bumped = False
    if bump_token != "none":
        manifest_path = Path(str(project["source_path"])) / "project.yaml"
        original_manifest = manifest_path.read_bytes()
        replacement = {
            key: value
            for key, value in project.items()
            if key not in {"ref", "manifest_digest", "source_path"}
        }
        replacement["version"] = bump_version(
            str(project.get("version") or "0.0.0"),
            bump_indexes[bump_token],
        )
        project = compositions.replace(
            project_id,
            replacement,
            expected_manifest_digest=str(project["manifest_digest"]),
        )
        bumped = True
    ctx = get_ctx()
    config = getattr(ctx, "config", None)
    subnet_id = str(getattr(config, "subnet_id", "") or "local").strip()
    node_settings = getattr(config, "node_settings", None)
    node_id = str(
        getattr(node_settings, "id", "")
        or getattr(config, "node_id", "")
        or "local"
    ).strip()
    workspace_root = _dev_workspace_root(service)
    try:
        source = _snapshot(service, project_id)
        payload = project_cli._build_project_release(
            project_id,
            revision=str(source["source_revision"]),
            repository=repository or f"adaos-dev:{subnet_id}:{node_id}",
            forge="content-addressed-dev",
            workspace_root=workspace_root,
            builder="adaos.dev.project.push",
        )
        payload["source_revision"] = source["source_revision"]
        payload["source_snapshot"] = {
            "file_count": source["file_count"],
            "size_bytes": source["size_bytes"],
            "components": source["components"],
        }
        payload["publication_stage"] = str(
            (project.get("publication") or {}).get("stage") or "alpha"
        )
        payload["version_bump"] = bump_token
        if not local_only:
            payload["publication"] = project_cli._publish_project_release(
                payload,
                workspace_root=workspace_root,
            )
    except Exception:
        if bumped and manifest_path is not None and original_manifest is not None:
            temporary = manifest_path.with_name(f".{manifest_path.name}.push-rollback")
            temporary.write_bytes(original_manifest)
            temporary.replace(manifest_path)
        raise
    _echo(payload, json_output=json_output)


__all__ = ["app"]
