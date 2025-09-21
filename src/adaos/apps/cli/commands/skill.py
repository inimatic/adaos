# src\adaos\apps\cli\commands\skill.py
from __future__ import annotations

import json
import os
from pathlib import Path
import traceback

import typer

from adaos.sdk.data.i18n import _
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.runtime import (
    SkillPrepError,
    SkillPrepMissingFunctionError,
    SkillPrepScriptNotFoundError,
    SkillRuntimeError,
    run_skill_handler_sync,
    run_skill_prep,
)
from adaos.services.skill.scaffold import create as scaffold_create
from adaos.adapters.db import SqliteSkillRegistry

app = typer.Typer(help=_("cli.help_skill"))


def _run_safe(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if os.getenv("ADAOS_CLI_DEBUG") == "1":
                traceback.print_exc()
            raise

    return wrapper


def _mgr() -> SkillManager:
    ctx = get_ctx()
    repo = ctx.skills_repo
    reg = SqliteSkillRegistry(ctx.sql)
    return SkillManager(repo=repo, registry=reg, git=ctx.git, paths=ctx.paths, bus=getattr(ctx, "bus", None), caps=ctx.caps)


@_run_safe
@app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json", help=_("cli.option.json")),
    show_fs: bool = typer.Option(False, "--fs", help=_("cli.option.fs")),
):
    """
    Список установленных навыков из реестра.
    JSON-формат: {"skills": [{"name": "...", "version": "..."}, ...]}
    """
    mgr = _mgr()
    rows = mgr.list_installed()  # SkillRecord[]

    if json_output:
        payload = {
            "skills": [
                {
                    "name": r.name,
                    # тестам важен только name, но version полезно оставить
                    "version": getattr(r, "active_version", None) or "unknown",
                }
                for r in rows
                # оставляем только действительно установленные (если поле есть)
                if bool(getattr(r, "installed", True))
            ]
        }
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return

    if not rows:
        typer.echo(_("skill.list.empty"))
    else:
        for r in rows:
            if not bool(getattr(r, "installed", True)):
                continue
            av = getattr(r, "active_version", None) or "unknown"
            typer.echo(_("cli.skill.list.item", name=r.name, version=av))

    if show_fs:
        present = {m.id.value for m in mgr.list_present()}
        desired = {r.name for r in rows if bool(getattr(r, "installed", True))}
        missing = desired - present
        extra = present - desired
        if missing:
            typer.echo(_("cli.skill.fs_missing", items=", ".join(sorted(missing))))
        if extra:
            typer.echo(_("cli.skill.fs_extra", items=", ".join(sorted(extra))))


@_run_safe
@app.command("sync")
def sync():
    """Применяет sparse-set к набору из реестра и делает pull."""
    mgr = _mgr()
    mgr.sync()
    typer.echo(_("cli.skill.sync.done"))


@_run_safe
@app.command("uninstall")
def uninstall(name: str):
    mgr = _mgr()
    mgr.uninstall(name)
    typer.echo(_("cli.skill.uninstall.done", name=name))


@_run_safe
@app.command("reconcile-fs-to-db")
def reconcile_fs_to_db():
    """Обходит {skills_dir} и проставляет installed=1 для найденных папок (кроме .git).
    Не трогает active_version/repo_url.
    """
    mgr = _mgr()
    ctx = get_ctx()
    root = Path(ctx.paths.skills_dir())
    if not root.exists():
        typer.echo(_("cli.skill.reconcile.missing_root"))
        raise typer.Exit(1)
    found = []
    for name in os.listdir(root):
        if name == ".git":
            continue
        p = root / name
        if p.is_dir():
            mgr.reg.register(name)  # installed=1
            found.append(name)
    typer.echo(
        _(
            "cli.skill.reconcile.added",
            items=", ".join(found) if found else _("cli.skill.reconcile.empty"),
        )
    )


@_run_safe
@app.command("push")
def push_command(
    skill_name: str = typer.Argument(..., help=_("cli.skill.push.name_help")),
    message: str = typer.Option(..., "--message", "-m", help=_("cli.commit_message.help")),
    signoff: bool = typer.Option(False, "--signoff", help=_("cli.option.signoff")),
):
    """
    Закоммитить изменения ТОЛЬКО внутри подпапки навыка и выполнить git push.
    Защищён политиками: skills.manage + git.write + net.git.
    """
    mgr = _mgr()
    res = mgr.push(skill_name, message, signoff=signoff)
    if res in {"nothing-to-push", "nothing-to-commit"}:
        typer.echo(_("cli.skill.push.nothing"))
    else:
        typer.echo(_("cli.skill.push.done", name=skill_name, revision=res))


@_run_safe
@app.command("create")
def cmd_create(name: str, template: str = typer.Option("demo_skill", "--template", "-t")):
    p = scaffold_create(name, template=template)
    typer.echo(_("cli.skill.create.created", path=p))


@_run_safe
@app.command("install")
def cmd_install(name: str):
    mgr = _mgr()
    result = mgr.install(name, validate=False)
    if isinstance(result, tuple):
        meta, report = result
    elif hasattr(result, "id"):
        meta, report = result, None
    else:
        typer.echo(str(result))
        return
    typer.echo(
        _(
            "cli.skill.install.done",
            name=meta.id.value if hasattr(meta, "id") else name,
            version=getattr(meta, "version", ""),
            path=getattr(meta, "path", ""),
        )
    )
    if report is not None and hasattr(report, "ok") and not report.ok:
        typer.echo(str(report))


@app.command("run")
def run(
    skill: str = typer.Argument(..., help=_("cli.skill.run.name_help")),
    topic: str = typer.Option("nlp.intent.weather.get", "--topic", "-t", help=_("cli.skill.run.topic_help")),
    payload: str = typer.Option("{}", "--payload", "-p", help=_("cli.skill.run.payload_help")),
):
    """Execute a skill handler locally using the configured workspace."""

    try:
        payload_obj = json.loads(payload) if payload else {}
        if not isinstance(payload_obj, dict):
            raise ValueError(_("cli.skill.run.payload_type_error"))
    except Exception as exc:
        raise typer.BadParameter(_("cli.skill.run.payload_invalid", error=str(exc)))

    try:
        result = run_skill_handler_sync(skill, topic, payload_obj)
    except SkillRuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(_("cli.skill.run.success", result=repr(result)))


@app.command("prep")
def prep_command(skill_name: str):
    """Запуск стадии подготовки (discover) для навыка"""
    try:
        result = run_skill_prep(skill_name)
    except SkillPrepScriptNotFoundError:
        print(f"[red]{_('skill.prep.not_found', skill_name=skill_name)}[/red]")
        raise typer.Exit(code=1)
    except SkillPrepMissingFunctionError:
        print(f"[red]{_('skill.prep.missing_func', skill_name=skill_name)}[/red]")
        raise typer.Exit(code=1)
    except SkillPrepError as exc:
        print(f"[red]{_('skill.prep.failed', reason=str(exc))}[/red]")
        raise typer.Exit(code=1)

    if result.get("status") == "ok":
        print(f"[green]{_('skill.prep.success', skill_name=skill_name)}[/green]")
    else:
        reason = result.get("reason", "unknown")
        print(f"[red]{_('skill.prep.failed', reason=reason)}[/red]")
