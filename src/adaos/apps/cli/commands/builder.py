from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

from adaos.apps.cli.commands.dev import _load_dev_scenario_model, _resolve_dev_scenario_file
from adaos.apps.cli.commands.skill import _mgr
from adaos.services.agent_context import get_ctx
from adaos.services.builder import BuilderWorkspaceService
from adaos.services.node_config import displayable_path
from adaos.services.root.service import (
    ArtifactCreateResult,
    ArtifactListItem,
    ArtifactPushResult,
    RootDeveloperService,
    RootServiceError,
    TemplateResolutionError,
)
from adaos.sdk.scenarios.runtime import ScenarioRuntime


app = typer.Typer(help="Builder authoring, draft, and preview workflows.")


def _service() -> RootDeveloperService:
    return RootDeveloperService()


def _display_path(path: Path | str | None) -> str:
    if path is None:
        return "-"
    rendered = displayable_path(Path(path))
    return rendered if rendered is not None else str(path)


def _normalize_artifact_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized in {"skill", "skills"}:
        return "skill"
    if normalized in {"scenario", "scenarios"}:
        return "scenario"
    raise typer.BadParameter("--kind must be skill or scenario")


def _create_result_payload(result: ArtifactCreateResult) -> dict[str, Any]:
    return {
        "ok": True,
        "kind": result.kind,
        "name": result.name,
        "owner_id": result.owner_id,
        "path": str(result.path),
        "version": result.version,
        "updated_at": result.updated_at,
    }


def _push_result_payload(result: ArtifactPushResult) -> dict[str, Any]:
    return {
        "ok": True,
        "kind": result.kind,
        "name": result.name,
        "stored_path": result.stored_path,
        "sha256": result.sha256,
        "bytes_uploaded": result.bytes_uploaded,
        "version": result.version,
        "updated_at": result.updated_at,
    }


def _list_item_payload(item: ArtifactListItem) -> dict[str, Any]:
    return {
        "name": item.name,
        "path": str(item.path),
        "version": item.version,
        "updated_at": item.updated_at,
    }


def _echo_create_result(result: ArtifactCreateResult, json_output: bool) -> None:
    payload = _create_result_payload(result)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.secho(f"{result.kind.title()} '{result.name}' created in Builder dev workspace.", fg=typer.colors.GREEN)
    typer.echo(f"Location: {_display_path(result.path)}")
    if result.version:
        typer.echo(f"Version: {result.version}")


def _echo_push_result(result: ArtifactPushResult, json_output: bool) -> None:
    payload = _push_result_payload(result)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.secho(f"{result.kind.title()} '{result.name}' uploaded to Forge.", fg=typer.colors.GREEN)
    typer.echo(f"Stored path: {result.stored_path}")
    typer.echo(f"SHA256: {result.sha256}")
    typer.echo(f"Bytes uploaded: {result.bytes_uploaded}")


def _echo_list_result(items: list[ArtifactListItem], json_output: bool) -> None:
    payload = [_list_item_payload(item) for item in items]
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not items:
        typer.echo("No Builder dev artifacts found.")
        return
    headers = ["Name", "Version", "Updated", "Path"]
    rows = [
        [
            item.name,
            item.version or "-",
            item.updated_at or "-",
            _display_path(item.path),
        ]
        for item in items
    ]
    widths = [max(len(str(row[i])) for row in [headers] + rows) for i in range(len(headers))]
    typer.echo("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    typer.echo("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        typer.echo("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def _echo_approval_profiles(profiles: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps({"ok": True, "profiles": profiles}, ensure_ascii=False, indent=2))
        return
    headers = ["Profile", "Auto draft", "Auto apply", "Review", "Summary"]
    rows = [
        [
            item.get("id", ""),
            "yes" if item.get("auto_draft") else "no",
            "yes" if item.get("auto_apply") else "no",
            item.get("requires_human_review", ""),
            item.get("summary", ""),
        ]
        for item in profiles
    ]
    widths = [max(len(str(row[i])) for row in [headers] + rows) for i in range(len(headers))]
    typer.echo("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    typer.echo("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        typer.echo("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def _resolve_dev_artifact_path(kind: str, artifact_id: str) -> Path | None:
    try:
        service = _service()
        items = service.list_skills() if kind == "skill" else service.list_scenarios()
    except Exception:
        return None
    target = str(artifact_id or "").strip()
    for item in items:
        if str(item.name or "").strip() == target:
            return Path(item.path).expanduser().resolve()
    return None


@app.command("create")
def create(
    artifact_id: str = typer.Argument(..., help="Skill or scenario id in the Builder/dev workspace."),
    kind: str = typer.Option("skill", "--kind", help="skill | scenario"),
    template: str | None = typer.Option(
        None,
        "--template",
        "-t",
        help="Template name. Defaults to the dev workflow default.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON response."),
) -> None:
    """Create a skill/scenario through the existing dev workspace lifecycle."""
    artifact_kind = _normalize_artifact_kind(kind)
    service = _service()
    try:
        result = (
            service.create_skill(artifact_id, template=template)
            if artifact_kind == "skill"
            else service.create_scenario(artifact_id, template=template)
        )
    except TemplateResolutionError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(exc.exit_code)
    except RootServiceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)
    _echo_create_result(result, json_output)


@app.command("list")
def list_cmd(
    kind: str = typer.Option("skill", "--kind", help="skill | scenario"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON response."),
) -> None:
    """List Builder/dev artifacts from the existing owner workspace."""
    artifact_kind = _normalize_artifact_kind(kind)
    service = _service()
    try:
        items = service.list_skills() if artifact_kind == "skill" else service.list_scenarios()
    except RootServiceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)
    _echo_list_result(items, json_output)


@app.command("approval-profiles")
def approval_profiles(
    json_output: bool = typer.Option(False, "--json", help="Print JSON response."),
) -> None:
    """List Builder approval profiles used by preview/review policy."""
    service = BuilderWorkspaceService.from_context()
    _echo_approval_profiles(service.approval_profiles(), json_output)


@app.command("push")
def push(
    artifact_id: str = typer.Argument(..., help="Skill or scenario id in the Builder/dev workspace."),
    kind: str = typer.Option("skill", "--kind", help="skill | scenario"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON response."),
) -> None:
    """Upload a Builder/dev artifact through the existing Forge dev push flow."""
    artifact_kind = _normalize_artifact_kind(kind)
    service = _service()
    try:
        result = service.push_skill(artifact_id) if artifact_kind == "skill" else service.push_scenario(artifact_id)
    except RootServiceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)
    _echo_push_result(result, json_output)


@app.command("validate")
def validate(
    artifact_id: str = typer.Argument(..., help="Skill or scenario id in the Builder/dev workspace."),
    kind: str = typer.Option("skill", "--kind", help="skill | scenario"),
    path: Path | None = typer.Option(None, "--path", help="Explicit artifact directory or scenario file."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON response."),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="Treat skill warnings as errors."),
    probe_tools: bool = typer.Option(False, "--probe-tools", help="Import skill handlers to verify tool exports."),
) -> None:
    """Validate a Builder/dev artifact using the existing dev validators."""
    artifact_kind = _normalize_artifact_kind(kind)
    if artifact_kind == "skill":
        mgr = _mgr()
        resolved_path = path if path is not None else _resolve_dev_artifact_path("skill", artifact_id)
        try:
            report = mgr.validate_skill(
                artifact_id,
                strict=strict,
                probe_tools=probe_tools,
                source="dev",
                path=resolved_path,
            )
        except Exception as exc:
            typer.secho(f"validate failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        issues = [asdict(issue) for issue in report.issues]
        if json_output:
            typer.echo(
                json.dumps(
                    {"ok": report.ok, "kind": "skill", "name": artifact_id, "issues": issues},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise typer.Exit(0 if report.ok else 1)
        if report.ok:
            typer.secho("validation passed", fg=typer.colors.GREEN)
            return
        for issue in report.issues:
            location = f" ({issue.where})" if getattr(issue, "where", None) else ""
            typer.echo(f"[{issue.level}] {issue.code}: {issue.message}{location}")
        raise typer.Exit(1)

    ctx = get_ctx()
    resolved_path = path if path is not None else _resolve_dev_artifact_path("scenario", artifact_id)
    base = Path(resolved_path).expanduser().resolve() if resolved_path is not None else ctx.paths.dev_scenarios_dir()
    scenario_file = _resolve_dev_scenario_file(artifact_id, base)
    if scenario_file is None or not scenario_file.exists():
        target = path or artifact_id
        typer.secho(f"Scenario file not found: {target}", fg=typer.colors.RED)
        raise typer.Exit(1)
    try:
        model = _load_dev_scenario_model(scenario_file)
        errors = ScenarioRuntime().validate(model)
    except Exception as exc:
        typer.secho(f"validate failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    if json_output:
        payload = {"ok": not bool(errors), "kind": "scenario", "scenario_id": model.id, "errors": errors}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        raise typer.Exit(0 if not errors else 1)
    if errors:
        typer.secho("Validation failed:", fg=typer.colors.RED)
        for err in errors:
            typer.echo(f"- {err}")
        raise typer.Exit(1)
    typer.secho(f"Scenario '{model.id}' is valid.", fg=typer.colors.GREEN)


def _read_json_arg(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    raw = str(value).strip()
    path = Path(raw[1:] if raw.startswith("@") else raw).expanduser()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise typer.BadParameter("JSON argument must be an object")
    return data


def _read_idea_arg(value: str | None, extra: list[str]) -> str:
    unexpected_options = [part for part in extra if str(part).startswith("-")]
    if unexpected_options:
        raise typer.BadParameter(f"unexpected option(s): {' '.join(unexpected_options)}")
    parts = [str(value).strip()] if value and str(value).strip() else []
    parts.extend(str(part).strip() for part in extra if str(part).strip())
    idea = " ".join(parts).strip()
    if not idea:
        raise typer.BadParameter("--idea is required")
    return idea


@app.command("draft", context_settings={"allow_extra_args": True, "ignore_unknown_options": False})
def draft(
    ctx: typer.Context,
    artifact_id: str = typer.Argument(..., help="Target skill/scenario id or descriptor-fix target id."),
    idea: str | None = typer.Option(None, "--idea", "-i", help="Human-readable source idea or requested behavior."),
    kind: str = typer.Option("skill", "--kind", help="skill | scenario | descriptor_fix"),
    task_id: str | None = typer.Option(None, "--task-id", help="Existing Builder task id."),
    template_id: str | None = typer.Option(None, "--template", help="Template id for skill/scenario drafts."),
    target_kind: str | None = typer.Option(None, "--target-kind", help="descriptor_fix target kind: skill | scenario."),
    target_root: str | None = typer.Option(None, "--target-root", help="Explicit target root for descriptor_fix drafts."),
    descriptor_changes: str | None = typer.Option(None, "--descriptor-changes", help="JSON object or @path for descriptor_fix materialization."),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response."),
) -> None:
    source_idea = _read_idea_arg(idea, list(ctx.args))
    service = BuilderWorkspaceService.from_context()
    result = service.create_draft(
        kind=kind,
        artifact_id=artifact_id,
        source_idea=source_idea,
        task_id=task_id,
        template_id=template_id,
        target_kind=target_kind,
        target_root=target_root,
        descriptor_changes=_read_json_arg(descriptor_changes),
    )
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=True, indent=2))
        return
    draft_payload = result["draft"]
    typer.echo(f"draft_id: {draft_payload['draft_id']}")
    typer.echo(f"artifact: {draft_payload['artifact']['kind']}:{draft_payload['artifact']['id']}")
    typer.echo(f"root: {result['artifact_root']}")


@app.command("preview")
def preview(
    draft_id: str = typer.Argument(..., help="Builder draft id."),
    approval_profile: str | None = typer.Option(
        None,
        "--approval-profile",
        help="Approval profile: manual_only | low_risk_auto_draft | low_risk_auto_apply | restricted_maintenance_repair.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response."),
) -> None:
    service = BuilderWorkspaceService.from_context()
    result = service.preview(draft_id=draft_id, approval_profile=approval_profile)
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=True, indent=2))
        return
    preview_payload = result["preview"]
    summary = preview_payload.get("summary") or {}
    typer.echo(f"preview_id: {preview_payload['preview_id']}")
    typer.echo(f"changed_files: {summary.get('changed_files', 0)}")
    typer.echo(f"schema_ok: {summary.get('schema_ok')}")
    typer.echo(f"route_plan_ok: {summary.get('route_plan_ok')}")
    typer.echo(f"approval_profile: {summary.get('approval_profile')}")
    typer.echo(f"review_decision: {summary.get('review_decision')}")
    typer.echo(f"human_review_required: {summary.get('human_review_required')}")
