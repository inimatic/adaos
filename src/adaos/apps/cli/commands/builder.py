from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from adaos.services.builder import BuilderWorkspaceService


app = typer.Typer(help="Builder draft and preview workflows.")


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


@app.command("draft")
def draft(
    artifact_id: str = typer.Argument(..., help="Target skill/scenario id or descriptor-fix target id."),
    idea: str = typer.Option(..., "--idea", "-i", help="Human-readable source idea or requested behavior."),
    kind: str = typer.Option("skill", "--kind", help="skill | scenario | descriptor_fix"),
    task_id: str | None = typer.Option(None, "--task-id", help="Existing Builder task id."),
    template_id: str | None = typer.Option(None, "--template", help="Template id for skill/scenario drafts."),
    target_kind: str | None = typer.Option(None, "--target-kind", help="descriptor_fix target kind: skill | scenario."),
    target_root: str | None = typer.Option(None, "--target-root", help="Explicit target root for descriptor_fix drafts."),
    descriptor_changes: str | None = typer.Option(None, "--descriptor-changes", help="JSON object or @path for descriptor_fix materialization."),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response."),
) -> None:
    service = BuilderWorkspaceService.from_context()
    result = service.create_draft(
        kind=kind,
        artifact_id=artifact_id,
        source_idea=idea,
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
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response."),
) -> None:
    service = BuilderWorkspaceService.from_context()
    result = service.preview(draft_id=draft_id)
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=True, indent=2))
        return
    preview_payload = result["preview"]
    summary = preview_payload.get("summary") or {}
    typer.echo(f"preview_id: {preview_payload['preview_id']}")
    typer.echo(f"changed_files: {summary.get('changed_files', 0)}")
    typer.echo(f"schema_ok: {summary.get('schema_ok')}")
    typer.echo(f"route_plan_ok: {summary.get('route_plan_ok')}")
    typer.echo(f"human_review_required: {summary.get('human_review_required')}")
