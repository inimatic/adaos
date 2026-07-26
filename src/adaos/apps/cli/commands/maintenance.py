from __future__ import annotations

import json

import typer

import adaos.services.self_hygiene as self_hygiene
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline import run_artifact_retention


app = typer.Typer(help="Maintenance and self-hygiene operations.")


def _emit(payload: dict, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(json.dumps(payload, ensure_ascii=False))


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
