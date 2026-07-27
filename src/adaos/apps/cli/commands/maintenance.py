from __future__ import annotations

import json
from pathlib import Path

import typer

import adaos.services.self_hygiene as self_hygiene
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline import run_artifact_retention
from adaos.services.artifact_identity import (
    ArtifactIdentityDiagnosticError,
    explain_workspace_artifact_identity,
)


app = typer.Typer(help="Maintenance and self-hygiene operations.")


def _emit(payload: dict, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _root_developer_service():
    from adaos.services.root.service import RootDeveloperService

    return RootDeveloperService()


@app.command("status")
def status_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Show disk pressure and retention-policy status."""
    _emit(self_hygiene.status(), json_output=json_output)


@app.command("apply-retention")
def apply_retention_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned policy writes without changing files."),
    enable_timer: bool = typer.Option(True, "--timer/--no-timer", help="Install the systemd hygiene timer on Linux."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Deploy the AdaOS retention policy where the OS supports it."""
    payload = self_hygiene.apply_retention_policy(dry_run=dry_run, enable_timer=enable_timer)
    _emit(payload, json_output=json_output)


@app.command("run")
def run_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report cleanup candidates without deleting them."),
    pressure_only: bool = typer.Option(False, "--pressure-only", help="Skip cleanup unless disk pressure is detected."),
    include_pip_cache: bool = typer.Option(
        True,
        "--include-pip-cache/--no-include-pip-cache",
        help="Allow pip/uv cache cleanup.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Run safe AdaOS hygiene tasks."""
    payload = self_hygiene.run_hygiene(
        trigger="cli.maintenance.run",
        dry_run=dry_run,
        pressure_only=pressure_only,
        include_pip_cache=include_pip_cache,
    )
    _emit(payload, json_output=json_output)


@app.command("artifact-retention")
def artifact_retention_cmd(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the exact conservative cleanup plan; otherwise only report it.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Plan or explicitly apply artifact staging/package retention."""

    payload = run_artifact_retention(get_ctx(), dry_run=not apply)
    _emit(payload, json_output=json_output)


@app.command("artifact-identity")
def artifact_identity_cmd(
    name_or_id: str = typer.Argument(..., help="Registry name or canonical artifact id."),
    kind: str = typer.Option(..., "--kind", help="Artifact kind: scenario or skill."),
    channel: str = typer.Option("stable", "--channel", help="Registry channel to explain."),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        help="Explicit Workspace root; defaults to the active AdaOS Workspace.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit formatted JSON."),
) -> None:
    """Explain source, package, release, and activation identity without mutation."""

    workspace_root = Path(workspace or get_ctx().paths.workspace_dir())
    try:
        payload = explain_workspace_artifact_identity(
            workspace_root,
            kind=kind,
            name_or_id=name_or_id,
            channel=channel,
        )
    except (ValueError, FileNotFoundError, ArtifactIdentityDiagnosticError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("artifact-registry-reconcile")
def artifact_registry_reconcile_cmd(
    project_id: str = typer.Argument(..., help="Canonical subscribed project id."),
    kind: str = typer.Option(..., "--kind", help="Artifact kind: scenario or skill."),
    channel: str = typer.Option("stable", "--channel", help="Remote channel to verify."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the exact reviewed projection; otherwise only return a plan.",
    ),
    reviewed_plan_digest: str | None = typer.Option(
        None,
        "--reviewed-plan-digest",
        help="Exact plan digest returned by the preceding read-only invocation.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit formatted JSON."),
) -> None:
    """Verify remote release identity and explicitly repair registry discovery."""

    normalized_kind = str(kind or "").strip().lower().rstrip("s")
    if normalized_kind not in {"scenario", "skill"}:
        raise typer.BadParameter("kind must be scenario or skill", param_hint="--kind")
    service = _root_developer_service()
    try:
        if apply:
            reviewed = str(reviewed_plan_digest or "").strip().lower()
            if not reviewed:
                raise typer.BadParameter(
                    "--reviewed-plan-digest is required with --apply",
                    param_hint="--reviewed-plan-digest",
                )
            payload = service.apply_artifact_registry_reconciliation(
                normalized_kind,
                project_id,
                channel=channel,
                reviewed_plan_digest=reviewed,
            )
        else:
            payload = service.plan_artifact_registry_reconciliation(
                normalized_kind,
                project_id,
                channel=channel,
            )
    except typer.BadParameter:
        raise
    except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("artifact-registry-recover")
def artifact_registry_recover_cmd(
    project_id: str = typer.Argument(..., help="Canonical installed project id."),
    kind: str = typer.Option(..., "--kind", help="Artifact kind: scenario or skill."),
    channel: str = typer.Option("stable", "--channel", help="Missing remote channel to restore."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the exact reviewed remote recovery; otherwise only return a plan.",
    ),
    reviewed_plan_digest: str | None = typer.Option(
        None,
        "--reviewed-plan-digest",
        help="Exact recovery plan digest returned by the preceding read-only invocation.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit formatted JSON."),
) -> None:
    """Restore missing remote package/release/channel state from attested receipts."""

    normalized_kind = str(kind or "").strip().lower().rstrip("s")
    if normalized_kind not in {"scenario", "skill"}:
        raise typer.BadParameter("kind must be scenario or skill", param_hint="--kind")
    service = _root_developer_service()
    try:
        if apply:
            reviewed = str(reviewed_plan_digest or "").strip().lower()
            if not reviewed:
                raise typer.BadParameter(
                    "--reviewed-plan-digest is required with --apply",
                    param_hint="--reviewed-plan-digest",
                )
            payload = service.apply_artifact_remote_registry_recovery(
                normalized_kind,
                project_id,
                channel=channel,
                reviewed_plan_digest=reviewed,
            )
        else:
            payload = service.plan_artifact_remote_registry_recovery(
                normalized_kind,
                project_id,
                channel=channel,
            )
    except typer.BadParameter:
        raise
    except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("artifact-registry-revalidate")
def artifact_registry_revalidate_cmd(
    project_id: str = typer.Argument(..., help="Canonical installed project id."),
    kind: str = typer.Option(..., "--kind", help="Artifact kind: scenario or skill."),
    channel: str = typer.Option("stable", "--channel", help="Recovery channel identity."),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Run the exact installed release in a new isolated empty-data Workspace.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit formatted JSON."),
) -> None:
    """Create current-contract trial evidence for a legacy accepted release."""

    normalized_kind = str(kind or "").strip().lower().rstrip("s")
    if normalized_kind not in {"scenario", "skill"}:
        raise typer.BadParameter("kind must be scenario or skill", param_hint="--kind")
    if not confirm:
        raise typer.BadParameter(
            "--confirm is required to create an isolated revalidation Workspace",
            param_hint="--confirm",
        )
    try:
        payload = _root_developer_service().revalidate_artifact_remote_registry_recovery(
            normalized_kind,
            project_id,
            channel=channel,
        )
    except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)
