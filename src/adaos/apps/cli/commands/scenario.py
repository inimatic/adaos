"""Typer commands for managing and executing scenarios."""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Optional

import typer

from adaos.adapters.db import SqliteScenarioRegistry
from adaos.apps.cli.i18n import _
from adaos.apps.cli.git_status import (
    compute_path_status,
    fetch_remote,
    render_diff,
    resolve_base_ref,
)
from adaos.services.agent_context import get_ctx
from adaos.services.scenario.manager import ScenarioManager
from adaos.services.scenario.scaffold import create as scaffold_create
from adaos.sdk.scenarios.runtime import ScenarioRuntime, ensure_runtime_context, load_scenario

app = typer.Typer(help=_("cli.help_scenario"))


def _run_safe(func):
    """Wrap Typer callbacks to surface tracebacks when ADAOS_CLI_DEBUG=1."""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            if os.getenv("ADAOS_CLI_DEBUG") == "1":
                traceback.print_exc()
            raise

    return wrapper


def _mgr() -> ScenarioManager:
    ctx = get_ctx()
    repo = ctx.scenarios_repo
    reg = SqliteScenarioRegistry(ctx.sql)
    return ScenarioManager(repo=repo, registry=reg, git=ctx.git, paths=ctx.paths, bus=ctx.bus, caps=ctx.caps)


@_run_safe
@app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json", help=_("cli.option.json")),
    show_fs: bool = typer.Option(False, "--fs", help=_("cli.option.fs")),
):
    """List installed scenarios from the registry."""

    mgr = _mgr()
    rows = mgr.list_installed()

    if json_output:
        payload = {
            "scenarios": [
                {
                    "name": r.name,
                    "version": getattr(r, "active_version", None) or "unknown",
                }
                for r in rows
                if bool(getattr(r, "installed", True))
            ]
        }
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return

    if not rows:
        typer.echo(_("cli.scenario.list.empty"))
    else:
        for r in rows:
            if not bool(getattr(r, "installed", True)):
                continue
            version = getattr(r, "active_version", None) or "unknown"
            typer.echo(_("cli.scenario.list.item", name=r.name, version=version))

    if show_fs:
        present = {m.id.value for m in mgr.list_present()}
        desired = {r.name for r in rows if bool(getattr(r, "installed", True))}
        missing = desired - present
        extra = present - desired
        if missing:
            typer.echo(_("cli.scenario.fs_missing", items=", ".join(sorted(missing))))
        if extra:
            typer.echo(_("cli.scenario.fs_extra", items=", ".join(sorted(extra))))


@_run_safe
@app.command("status")
def status(
    name: Optional[str] = typer.Argument(None, help="scenario name (omit to report for all installed scenarios)"),
    remote: str = typer.Option("origin", "--remote", help="git remote name for comparison"),
    ref: Optional[str] = typer.Option(None, "--ref", help="base git ref (default: <remote>/HEAD or @{u})"),
    fetch: bool = typer.Option(False, "--fetch/--no-fetch", help="git fetch before comparing"),
    diff: bool = typer.Option(False, "--diff", help="print git diff vs base ref (requires NAME)"),
    json_output: bool = typer.Option(False, "--json", help=_("cli.option.json")),
):
    ctx = get_ctx()
    workspace_root = ctx.paths.workspace_dir()
    scenarios_root = ctx.paths.scenarios_workspace_dir()

    if diff and not name:
        typer.secho("--diff requires a specific scenario name", fg=typer.colors.RED)
        raise typer.Exit(2)

    if fetch:
        err = fetch_remote(workspace_root, remote=remote)
        if err:
            typer.secho(f"git fetch failed: {err}", fg=typer.colors.YELLOW)

    base_ref = (ref or "").strip() or resolve_base_ref(workspace_root, remote=remote)

    if name:
        names = [name]
    else:
        try:
            rows = SqliteScenarioRegistry(ctx.sql).list()
        except Exception:
            rows = []
        names = []
        for row in rows:
            n = getattr(row, "name", None) or getattr(row, "id", None)
            if not n or not bool(getattr(row, "installed", True)):
                continue
            names.append(str(n))
        names = sorted(set(names))

    rows_by_name = {}
    try:
        for r in SqliteScenarioRegistry(ctx.sql).list():
            rows_by_name[str(getattr(r, "name", None) or getattr(r, "id", ""))] = r
    except Exception:
        rows_by_name = {}

    results: list[dict] = []
    for scenario_name in names:
        row = rows_by_name.get(scenario_name)
        version = getattr(row, "active_version", None) if row is not None else None
        path_status = compute_path_status(
            workdir=workspace_root,
            path=(Path(scenarios_root) / scenario_name),
            base_ref=base_ref,
        )
        results.append(
            {
                "name": scenario_name,
                "version": version or "unknown",
                "git": {
                    "path": path_status.path,
                    "exists": path_status.exists,
                    "dirty": path_status.dirty,
                    "base_ref": path_status.base_ref,
                    "changed_vs_base": path_status.changed_vs_base,
                    "local_last_commit": (
                        {
                            "sha": path_status.local_last_commit.sha,
                            "timestamp": path_status.local_last_commit.timestamp,
                            "iso": path_status.local_last_commit.iso,
                            "subject": path_status.local_last_commit.subject,
                        }
                        if path_status.local_last_commit
                        else None
                    ),
                    "base_last_commit": (
                        {
                            "sha": path_status.base_last_commit.sha,
                            "timestamp": path_status.base_last_commit.timestamp,
                            "iso": path_status.base_last_commit.iso,
                            "subject": path_status.base_last_commit.subject,
                        }
                        if path_status.base_last_commit
                        else None
                    ),
                    "error": path_status.error,
                },
            }
        )

    if json_output:
        typer.echo(json.dumps({"scenarios": results}, ensure_ascii=False, indent=2))
        return

    if name:
        entry = results[0] if results else {}
        g = entry.get("git") or {}
        typer.echo(f"scenario: {entry.get('name')}")
        typer.echo(f"version: {entry.get('version')}")
        typer.echo(f"git path: {g.get('path')}")
        typer.echo(f"git base: {g.get('base_ref') or '(none)'}")
        if g.get("error"):
            typer.secho(f"git: {g.get('error')}", fg=typer.colors.YELLOW)
        else:
            flags: list[str] = []
            if g.get("dirty"):
                flags.append("dirty")
            if g.get("changed_vs_base"):
                flags.append("diff")
            typer.echo("git status: " + (", ".join(flags) if flags else "clean"))
            if g.get("local_last_commit"):
                lc = g["local_last_commit"]
                typer.echo(f"last local: {lc.get('sha')} {lc.get('iso') or lc.get('timestamp')} {lc.get('subject')}")
            if g.get("base_last_commit"):
                bc = g["base_last_commit"]
                typer.echo(f"last base:  {bc.get('sha')} {bc.get('iso') or bc.get('timestamp')} {bc.get('subject')}")

        if diff:
            if not base_ref:
                typer.secho("cannot diff: base ref is not available", fg=typer.colors.YELLOW)
            else:
                try:
                    typer.echo(render_diff(workspace_root, base_ref=base_ref, path=str(g.get("path") or "")))
                except Exception as exc:
                    typer.secho(f"diff failed: {exc}", fg=typer.colors.RED)
                    raise typer.Exit(1) from exc
        return

    for entry in results:
        g = entry.get("git") or {}
        flags: list[str] = []
        if g.get("dirty"):
            flags.append("dirty")
        if g.get("changed_vs_base"):
            flags.append("diff")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        typer.echo(f"{entry.get('name')}: v{entry.get('version')}{suffix}")


@_run_safe
@app.command("sync")
def sync_cmd():
    """Apply sparse checkout for scenarios and pull the repository."""

    mgr = _mgr()
    mgr.sync()
    typer.echo(_("cli.scenario.sync.done"))


@_run_safe
@app.command("install")
def install_cmd(
    name: str = typer.Argument(..., help=_("cli.scenario.install.name_help")),
    pin: Optional[str] = typer.Option(None, "--pin", help=_("cli.scenario.install.pin_help")),
):
    """Install a scenario into the workspace monorepo."""

    mgr = _mgr()
    # Stage A2: use extended install that also applies dependencies.
    meta = mgr.install_with_deps(name, pin=pin)
    typer.echo(_("cli.scenario.install.done", name=meta.id.value, version=meta.version, path=meta.path))


@_run_safe
@app.command("create")
def create_cmd(
    scenario_id: str = typer.Argument(..., help=_("cli.scenario.create.name_help")),
    template: str = typer.Option("scenario_default", "--template", "-t", help=_("cli.scenario.create.template_help")),
):
    """Create a new scenario scaffold from a template. Deprecated. Use adaos dev scenario create"""
    typer.secho("Deprecated. Use adaos dev scenario create.", fg=typer.colors.RED)
    raise typer.Exit(1)


@_run_safe
@app.command("uninstall")
def uninstall_cmd(
    name: str = typer.Argument(..., help=_("cli.scenario.uninstall.name_help")),
    safe: bool = typer.Option(False, "--safe", help=_("cli.scenario.uninstall.option.safe")),
):
    """Uninstall a scenario by removing it from registry and sparse checkout."""

    mgr = _mgr()
    mgr.uninstall(name, safe=safe)
    typer.echo(_("cli.scenario.uninstall.done", name=name))


@_run_safe
@app.command("push")
def push_command(
    scenario_name: str = typer.Argument(..., help=_("cli.scenario.push.name_help")),
    message: Optional[str] = typer.Option(None, "--message", "-m", help=_("cli.commit_message.help")),
    signoff: bool = typer.Option(False, "--signoff", help=_("cli.option.signoff")),
):
    """Commit changes inside a scenario directory and push to remote."""

    if message is None:
        typer.secho(
            "Root publishing via 'adaos scenario push' has moved to 'adaos dev scenario push'.",
            fg=typer.colors.YELLOW,
        )
        typer.echo("Use --message/-m to push commits or run 'adaos dev scenario push <name>'.")
        raise typer.Exit(1)

    mgr = _mgr()
    result = mgr.push(scenario_name, message, signoff=signoff)
    if result in {"nothing-to-push", "nothing-to-commit"}:
        typer.echo(_("cli.scenario.push.nothing"))
    else:
        typer.echo(_("cli.scenario.push.done", name=scenario_name, revision=result))


@_run_safe
@app.command("run")
def run_cmd(
    scenario_id: str = typer.Argument(..., help=_("cli.scenario.run.name_help")),
    path: Optional[str] = typer.Option(None, "--path", help=_("cli.scenario.run.path_help")),
) -> None:
    ctx = get_ctx()
    scenario_path = (path if path else ctx.paths.scenarios_workspace_dir()) / scenario_id
    runtime = ScenarioRuntime()
    result = runtime.run_from_file(str(scenario_path))
    meta = result.get("meta") or {}
    log_file = meta.get("log_file")
    typer.secho(_("cli.scenario.run.success", scenario_id=scenario_id), fg=typer.colors.GREEN)
    if log_file:
        typer.echo(_("cli.scenario.run.log", path=log_file))
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@_run_safe
@app.command("validate")
def validate_cmd(
    scenario_id: str = typer.Argument(..., help=_("cli.scenario.validate.name_help")),
    path: Optional[Path] = typer.Option(None, "--path", help=_("cli.scenario.validate.path_help")),
    json_output: bool = typer.Option(False, "--json", help="machine readable output"),
) -> None:
    """
    Validate scenario from workspace (default), dev space, or explicit --path.
    """
    ctx = get_ctx()
    if path:
        scenario_path = Path(path).expanduser().resolve()
        if scenario_path.is_dir():
            scenario_path = scenario_path
        else:
            # если указали путь до файла – поддержим и это
            scenario_path = scenario_path.parent
    else:
        scenario_path = ctx.paths.scenarios_workspace_dir()
    scenario_path = scenario_path / scenario_id
    model = load_scenario(scenario_path)
    runtime = ScenarioRuntime()
    errors = runtime.validate(model)

    if json_output:
        payload = {"ok": not bool(errors), "errors": errors, "scenario_id": model.id}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        raise typer.Exit(0 if not errors else 1)

    if errors:
        typer.secho(_("cli.scenario.validate.errors"), fg=typer.colors.RED)
        for err in errors:
            typer.echo(_("cli.scenario.validate.error_item", error=str(err)))
        raise typer.Exit(code=1)
    typer.secho(_("cli.scenario.validate.success", scenario_id=model.id), fg=typer.colors.GREEN)


def _collect_scenario_tests(scenario_id: Optional[str]) -> list[Path]:
    ctx = get_ctx()
    root = ctx.paths.scenarios_workspace_dir()
    tests: list[Path] = []
    if not root.exists():
        return tests
    if scenario_id:
        candidates = [root / scenario_id / "tests"]
    else:
        candidates = [p / "tests" for p in root.iterdir() if p.is_dir()]
    for tests_dir in candidates:
        if tests_dir.is_dir() and any(tests_dir.glob("test_*.py")):
            tests.append(tests_dir)
    return tests


@_run_safe
@app.command("test")
def test_cmd(
    scenario_id: Optional[str] = typer.Argument(None, help=_("cli.scenario.test.name_help")),
    extra: Optional[str] = typer.Option(None, "--pytest-args", help=_("cli.scenario.test.extra_help")),
) -> None:
    tests = _collect_scenario_tests(scenario_id)
    if not tests:
        typer.secho(_("cli.scenario.test.none"), fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    args = ["pytest", "-q", *[str(p) for p in tests]]
    if extra:
        args.extend(extra.split())

    command = " ".join(args)
    typer.echo(_("cli.scenario.test.running", command=command))
    result = subprocess.run(args, text=True)
    raise typer.Exit(code=result.returncode)


__all__ = ["app"]
