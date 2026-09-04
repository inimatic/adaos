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
from adaos.services.root.service import RootDeveloperService
from adaos.services.semver import bump_version
from adaos.sdk.developer import compositions


app = typer.Typer(help="Manage complete Projects in the local DEV workspace.")


def _service() -> BuilderWorkspaceService:
    return BuilderWorkspaceService.from_context()


def _root_service() -> RootDeveloperService:
    return RootDeveloperService()


def _dev_workspace_root(service: BuilderWorkspaceService) -> Path:
    return service._dev_projects_root().parent


def _echo(payload: Any, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, Mapping):
        record = (
            payload.get("project")
            if isinstance(payload.get("project"), Mapping)
            else payload
        )
        project_id = str(record.get("project_id") or record.get("id") or "")
        status = str(record.get("status") or payload.get("status") or "")
        version = str(record.get("version") or "")
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


def _echo_candidate(payload: Mapping[str, Any], *, json_output: bool) -> None:
    if json_output:
        _echo(payload, json_output=True)
        return
    candidate = (
        payload.get("candidate")
        if isinstance(payload.get("candidate"), Mapping)
        else payload
    )
    candidate_id = str(candidate.get("candidate_id") or "")
    project_id = str(candidate.get("project_id") or candidate.get("name") or "")
    version = str(candidate.get("version") or "")
    status = str(candidate.get("status") or payload.get("status") or "")
    phase = str(payload.get("lifecycle_phase") or "")
    typer.echo(
        " ".join(
            item for item in (candidate_id, project_id, version, status, phase) if item
        )
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


@app.command("create")
def create_project(
    project_id: str,
    primary_kind: str = typer.Option(
        "scenario",
        "--primary-kind",
        help="Primary component kind: scenario or skill.",
    ),
    primary_id: str | None = typer.Option(
        None,
        "--primary-id",
        help="Primary component id. Defaults to the Project id.",
    ),
    title: str | None = typer.Option(None, "--title"),
    description: str = typer.Option("", "--description"),
    template: str | None = typer.Option(None, "--template"),
    existing: bool = typer.Option(
        False,
        "--existing",
        help="Create Project authority around an existing unowned DEV component.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    kind = str(primary_kind or "").strip().lower().rstrip("s")
    if kind not in {"scenario", "skill"}:
        raise typer.BadParameter(
            "primary kind must be scenario or skill",
            param_hint="--primary-kind",
        )
    component_id = str(primary_id or project_id).strip()
    entrypoints = (
        [
            {
                "id": "main",
                "presentation": f"scenario:{component_id}",
                "default": True,
                "bindings": {},
            }
        ]
        if kind == "scenario"
        else []
    )
    try:
        if existing:
            result = compositions.create_for_existing_component(
                project_id,
                kind=kind,
                component_id=component_id,
                title=title,
                description=description or None,
                entrypoints=entrypoints,
                actor="adaos.dev.project.create",
            )
        else:
            result = compositions.create_with_primary_component(
                project_id,
                kind=kind,
                component_id=component_id,
                template=template,
                title=title or project_id,
                description=description,
                entrypoints=entrypoints,
                actor="adaos.dev.project.create",
            )
    except Exception as exc:
        raise typer.BadParameter(str(exc), param_hint="project_id") from exc
    _echo(result, json_output=json_output)


@app.command("attach")
def attach_component(
    project_id: str,
    component_ref: str,
    role: str = typer.Option("implementation", "--role"),
    exposure: str = typer.Option("project_only", "--exposure"),
    lifecycle: str = typer.Option("bound", "--lifecycle"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Attach an existing DEV skill or scenario to one Project authority."""

    try:
        result = compositions.ensure_owned_component(
            project_id,
            component_ref,
            role=role,
            exposure=exposure,
            lifecycle=lifecycle,
        )
    except Exception as exc:
        raise typer.BadParameter(str(exc), param_hint="component_ref") from exc
    _echo(result, json_output=json_output)


@app.command("depend")
def add_dependency(
    project_id: str,
    dependency_ref: str,
    version: str | None = typer.Option(None, "--version"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Declare an existing shared Project, skill, or scenario dependency."""

    try:
        result = compositions.ensure_dependency(
            project_id,
            dependency_ref,
            version=version,
        )
    except Exception as exc:
        raise typer.BadParameter(str(exc), param_hint="dependency_ref") from exc
    _echo(result, json_output=json_output)


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


@app.command("fork")
def fork(
    project_id: str,
    actor: str = typer.Option("user:local", "--actor"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Fork the complete authoritative Workspace Project slice into DEV."""

    try:
        payload = _service().create_project_local_fork(project_id, actor=actor)
    except Exception as exc:
        raise typer.BadParameter(str(exc), param_hint="project_id") from exc
    _echo(payload, json_output=json_output)


@app.command("checkpoint")
def checkpoint(
    project_id: str,
    change_id: str = typer.Option(..., "--change-id"),
    message: str | None = typer.Option(None, "--message", "-m"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Checkpoint every owned Project component in the local Forge."""

    project = _project(project_id)
    change_token = str(change_id or "").strip()
    if not change_token:
        raise typer.BadParameter("change id is required", param_hint="--change-id")
    service = _service()
    checkpoints: list[dict[str, Any]] = []
    for item in (project.get("components") or {}).get("owned") or []:
        component_ref = str(item.get("ref") or "").strip()
        kind, separator, artifact_id = component_ref.partition(":")
        if separator != ":" or kind not in {"skill", "scenario"} or not artifact_id:
            raise typer.BadParameter(
                f"unsupported owned component ref: {component_ref!r}",
                param_hint="project_id",
            )
        checkpoints.append(
            service.checkpoint_artifact(
                kind=kind,
                artifact_id=artifact_id,
                message=message or f"checkpoint(project): {project_id} {change_token}",
                metadata={
                    "project_id": project_id,
                    "project_ref": f"project:{project_id}",
                    "change_id": change_token,
                },
            )
        )
    failures = [item for item in checkpoints if not bool(item.get("ok"))]
    payload = {
        "ok": not failures,
        "schema": "adaos.dev.project_checkpoint.v1",
        "status": "checkpointed" if not failures else "checkpoint_failed",
        "project_id": project_id,
        "change_id": change_token,
        "components": checkpoints,
    }
    _echo(payload, json_output=json_output)
    if failures:
        raise typer.Exit(1)


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
    """Build and optionally upload an immutable ProjectRelease checkpoint."""

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


@app.command("trial")
def prepare_trial(
    project_id: str,
    change_ids: list[str] = typer.Option(
        ...,
        "--change-id",
        help="Builder Change id; repeat for a batched Project change set.",
    ),
    evidence_refs: list[str] | None = typer.Option(
        None,
        "--evidence",
        help="Validation evidence ref; repeat as needed.",
    ),
    target_webspace_id: str = typer.Option("desktop-dev", "--webspace"),
    target_space_kind: str = typer.Option("development", "--space-kind"),
    target_zone: str | None = typer.Option(None, "--zone"),
    target_subnet_id: str | None = typer.Option(None, "--subnet"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Create the alpha runtime Trial from the exact Project checkpoint."""

    bounded_changes = tuple(
        dict.fromkeys(str(item).strip() for item in change_ids if str(item).strip())
    )
    if not bounded_changes:
        raise typer.BadParameter("at least one change id is required", param_hint="--change-id")
    refs = [str(item).strip() for item in evidence_refs or [] if str(item).strip()]
    result = _root_service().prepare_project_candidate_from_primary_checkpoint(
        project_id,
        change_ids=bounded_changes,
        validation_evidence={
            "status": "passed",
            "validator": "adaos.dev.project.trial",
            "refs": refs,
        },
        target_webspace_id=target_webspace_id,
        target_space_kind=target_space_kind,
        target_zone=target_zone,
        target_subnet_id=target_subnet_id,
        idempotency_key=idempotency_key,
    )
    result.setdefault("lifecycle_phase", "alpha")
    _echo_candidate(result, json_output=json_output)


@app.command("candidate")
def candidate(
    candidate_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    result = _root_service().get_artifact_candidate(candidate_id)
    _echo_candidate(result, json_output=json_output)


@app.command("trial-decide")
def decide_trial(
    candidate_id: str,
    decision: str = typer.Argument(..., help="accept or reject"),
    actor: str = typer.Option("user:local", "--actor"),
    evidence_refs: list[str] | None = typer.Option(None, "--evidence"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Accept an exact Trial as beta, or request further changes."""

    normalized = str(decision or "").strip().lower()
    if normalized not in {"accept", "reject"}:
        raise typer.BadParameter("decision must be accept or reject", param_hint="decision")
    refs = [str(item).strip() for item in evidence_refs or [] if str(item).strip()]
    result = _root_service().decide_artifact_candidate(
        candidate_id,
        accepted=normalized == "accept",
        observations=(
            {
                "actor": actor,
                "decision": "accepted" if normalized == "accept" else "changes_requested",
                "evidence": refs,
            },
        ),
    )
    result.setdefault(
        "lifecycle_phase",
        "beta" if normalized == "accept" else "changes_requested",
    )
    _echo_candidate(result, json_output=json_output)


@app.command("promote")
def promote(
    candidate_id: str,
    confirmed: bool = typer.Option(
        False,
        "--confirm",
        help="Confirm stable channel and Workspace activation.",
    ),
    actor: str = typer.Option("user:local", "--actor"),
    approval_id: str | None = typer.Option(None, "--approval-id"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Promote an accepted beta candidate into the local Workspace."""

    if not confirmed:
        raise typer.BadParameter(
            "promotion requires explicit --confirm",
            param_hint="--confirm",
        )
    result = _root_service().promote_artifact_candidate(
        candidate_id,
        permission_decision={
            "approved": True,
            "actor": actor,
            "actor_type": "user",
            "approval_id": approval_id or f"candidate:{candidate_id}:promotion",
        },
    )
    result.setdefault("lifecycle_phase", "workspace")
    _echo_candidate(result, json_output=json_output)


@app.command("publish")
def publish(
    candidate_id: str,
    confirmed: bool = typer.Option(
        False,
        "--confirm",
        help="Confirm publication of the promoted source to adaos-registry.",
    ),
    remote: str = typer.Option("origin", "--remote"),
    branch: str = typer.Option("main", "--branch"),
    message: str | None = typer.Option(None, "--message", "-m"),
    signoff: bool = typer.Option(False, "--signoff"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Publish the promoted exact source closure to adaos-registry."""

    if not confirmed:
        raise typer.BadParameter(
            "source registry publication requires explicit --confirm",
            param_hint="--confirm",
        )
    result = _root_service().publish_project_candidate_source(
        candidate_id,
        remote=remote,
        branch=branch,
        message=message,
        signoff=signoff,
    )
    _echo_candidate(result, json_output=json_output)


__all__ = ["app"]
