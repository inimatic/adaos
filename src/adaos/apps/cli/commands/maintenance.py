from __future__ import annotations

import json
from pathlib import Path

import typer

import adaos.services.self_hygiene as self_hygiene
import adaos.services.storage_hygiene as storage_hygiene
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
    if dry_run:
        payload["storage_jobs"] = storage_hygiene.list_storage_cleanup_jobs(
            workspace_root=Path.cwd(),
            base_dir=payload.get("base_dir"),
        )
    else:
        payload["storage_jobs"] = storage_hygiene.resume_pending_storage_jobs(
            workspace_root=Path.cwd(),
            base_dir=payload.get("base_dir"),
            max_jobs=3,
            max_seconds=2.0,
            max_entries=500,
        )
    _emit(payload, json_output=json_output)


@app.command("inspect")
def inspect_storage_cmd(
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    max_entries: int = typer.Option(1000, "--max-entries", min=1, help="Maximum immediate entries counted per managed root."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Inspect storage roles without recursively walking managed trees."""

    payload = storage_hygiene.inspect_storage(
        workspace_root=workspace_root,
        base_dir=base_dir,
        max_entries_per_root=max_entries,
    )
    _emit(payload, json_output=json_output)


@app.command("workspace-check")
def workspace_check_cmd(
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root"),
    reserve_gib: float = typer.Option(0, "--reserve-gib", min=0, help="Expected extra space for the next operation."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Check scratch quota and free space before creating worktrees or builds."""
    from adaos.services.workspace_storage import GIB, inspect_workspace_storage

    try:
        payload = inspect_workspace_storage(workspace_root, reserve_bytes=int(reserve_gib * GIB))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)
    if not payload["ok"]:
        raise typer.Exit(1)


@app.command("workspace-clean")
def workspace_clean_cmd(
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root"),
    base_dir: Path | None = typer.Option(None, "--base-dir"),
    apply: bool = typer.Option(False, "--apply", help="Remove only expired, completed, managed scratch and eligible Git worktrees."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Preview or execute bounded developer cleanup; legacy data stays protected."""
    from adaos.services.workspace_storage import maintain_workspace_storage

    try:
        payload = maintain_workspace_storage(workspace_root, base_dir=base_dir, apply=apply)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)
    if not payload["ok"] or not payload["report"]["ok"]:
        raise typer.Exit(1)


@app.command("plan")
def plan_storage_cmd(
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    profile: str = typer.Option("normal", "--profile", help="Retention profile: normal or pressure."),
    include_legacy: bool = typer.Option(False, "--include-legacy", help="Allow age-based planning for entries without manifests."),
    max_candidates: int = typer.Option(2000, "--max-candidates", min=1, help="Bound the number of planned candidates."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Create and persist a deterministic cleanup plan without deleting data."""

    try:
        payload = storage_hygiene.plan_storage_cleanup(
            workspace_root=workspace_root,
            base_dir=base_dir,
            profile=profile,
            include_legacy=include_legacy,
            max_candidates=max_candidates,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("apply")
def apply_storage_cmd(
    plan_digest: str = typer.Argument(..., help="Exact digest returned by maintenance plan."),
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    max_seconds: float = typer.Option(2.0, "--max-seconds", min=0.05, help="Maximum deletion time for this invocation."),
    max_entries: int = typer.Option(500, "--max-entries", min=1, help="Maximum filesystem entries removed in this invocation."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Quarantine an exact cleanup plan and perform one bounded deletion slice."""

    try:
        payload = storage_hygiene.apply_storage_cleanup_plan(
            plan_digest,
            workspace_root=workspace_root,
            base_dir=base_dir,
            max_seconds=max_seconds,
            max_entries=max_entries,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("resume")
def resume_storage_cmd(
    job_id: str = typer.Argument(..., help="Cleanup job id returned by maintenance apply."),
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    max_seconds: float = typer.Option(2.0, "--max-seconds", min=0.05, help="Maximum deletion time for this invocation."),
    max_entries: int = typer.Option(500, "--max-entries", min=1, help="Maximum filesystem entries removed in this invocation."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Continue a bounded cleanup job from its persisted checkpoint."""

    try:
        payload = storage_hygiene.resume_storage_cleanup(
            job_id,
            workspace_root=workspace_root,
            base_dir=base_dir,
            max_seconds=max_seconds,
            max_entries=max_entries,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("jobs")
def storage_jobs_cmd(
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    limit: int = typer.Option(100, "--limit", min=1, help="Maximum recent jobs returned."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """List persisted cleanup jobs and their resumable status."""

    payload = storage_hygiene.list_storage_cleanup_jobs(
        workspace_root=workspace_root,
        base_dir=base_dir,
        limit=limit,
    )
    _emit(payload, json_output=json_output)


@app.command("explain")
def explain_storage_cmd(
    path: Path = typer.Argument(..., help="Path inside the development Workspace."),
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Explain ownership, retention, pin, manifest, and lease state for a path."""

    try:
        payload = storage_hygiene.explain_storage_path(path, workspace_root=workspace_root, base_dir=base_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("pin")
def pin_storage_cmd(
    path: Path = typer.Argument(..., help="Path inside the development Workspace."),
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    reason: str = typer.Option("manual", "--reason", help="Human-readable retention reason."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Pin a path and its descendants against automatic cleanup."""

    try:
        payload = storage_hygiene.pin_storage_path(path, workspace_root=workspace_root, base_dir=base_dir, reason=reason)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("adopt")
def adopt_storage_cmd(
    path: Path = typer.Argument(..., help="Legacy path inside an automatically managed storage root."),
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    owner: str | None = typer.Option(None, "--owner", help="Producer owner; defaults to the catalog owner."),
    retention_class: str | None = typer.Option(None, "--retention-class", help="Retention class; defaults to the catalog class."),
    retain_days: float | None = typer.Option(None, "--retain-days", min=0.0, help="Retention in days; defaults to the catalog policy."),
    pinned: bool = typer.Option(False, "--pin", help="Pin the adopted artifact against cleanup."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Attach an owner and retention manifest to a legacy artifact."""

    try:
        payload = storage_hygiene.adopt_legacy_storage_path(
            path,
            workspace_root=workspace_root,
            base_dir=base_dir,
            owner=owner,
            retention_class=retention_class,
            retain_seconds=None if retain_days is None else int(retain_days * 86400),
            pinned=pinned,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("unpin")
def unpin_storage_cmd(
    path: Path = typer.Argument(..., help="Path inside the development Workspace."),
    workspace_root: Path = typer.Option(Path.cwd(), "--workspace-root", help="Repository or development Workspace root."),
    base_dir: Path | None = typer.Option(None, "--base-dir", help="AdaOS base directory; defaults to <workspace>/.adaos."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Remove an explicit automatic-cleanup pin."""

    try:
        payload = storage_hygiene.unpin_storage_path(path, workspace_root=workspace_root, base_dir=base_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(payload, json_output=json_output)


@app.command("skill-factory-compact")
def compact_skill_factory_cmd(
    apply: bool = typer.Option(False, "--apply", help="Archive the exact reviewed terminal-task plan."),
    reviewed_plan_digest: str | None = typer.Option(None, "--reviewed-plan-digest", help="Digest returned by the preceding read-only invocation."),
    state_dir: Path | None = typer.Option(None, "--state-dir", help="AdaOS state directory; defaults to the active state directory."),
    keep_terminal: int = typer.Option(200, "--keep-terminal", min=0, help="Always retain this many newest terminal tasks in the hot state."),
    min_age_days: float = typer.Option(30.0, "--min-age-days", min=0.0, help="Minimum terminal-task age before archival."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Plan or apply bounded Skill Factory hot-state compaction."""

    from adaos.services.skill_factory import SkillFactoryService

    if apply and not str(reviewed_plan_digest or "").strip():
        raise typer.BadParameter("--reviewed-plan-digest is required with --apply")
    try:
        payload = SkillFactoryService(state_dir=state_dir).compact_state(
            apply=apply,
            reviewed_plan_digest=reviewed_plan_digest,
            keep_terminal=keep_terminal,
            min_age_seconds=min_age_days * 86400,
        )
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
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
