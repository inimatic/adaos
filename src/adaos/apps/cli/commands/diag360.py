from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from adaos.services.diag360 import create_360log_snapshot, list_360log_snapshots, load_360log_snapshot


app = typer.Typer(help="360log flight-recorder snapshots.")


def _print_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("snapshot")
def snapshot(
    reason: str | None = typer.Option(None, "--reason", "-r", help="Human-readable reason for this diagnostic snapshot."),
    scope: str = typer.Option("auto", "--scope", help="Log scope: auto, local, or subnet."),
    subnet_id: str | None = typer.Option(None, "--subnet-id", help="Subnet id for subnet scope."),
    webspace_id: str | None = typer.Option("desktop", "--webspace-id", help="Webspace id for runtime/Yjs diagnostics."),
    lines: int = typer.Option(300, "--lines", min=1, max=2000, help="Tail lines per log file."),
    files: int = typer.Option(8, "--files", min=1, max=50, help="Max log files per category."),
    timeout: float = typer.Option(2.0, "--timeout", min=0.2, max=15.0, help="Per-node collection timeout for subnet scope."),
    browser_log: Path | None = typer.Option(None, "--browser-log", help="Optional browser runtime-debug JSON/JSONL export to include."),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON result."),
) -> None:
    """Persist a bounded 360log snapshot and print its snapshot id."""
    result = create_360log_snapshot(
        reason=reason,
        scope=scope,
        subnet_id=subnet_id,
        webspace_id=webspace_id,
        lines=lines,
        files=files,
        timeout=timeout,
        browser_log_path=browser_log,
    )
    if json_output:
        _print_json(result)
        return
    typer.echo(f"snapshot_id={result.get('snapshot_id')}")
    typer.echo(f"path={result.get('path')}")
    typer.echo(f"timeline_path={result.get('timeline_path')}")
    typer.echo(f"items_total={result.get('items_total')}")


@app.command("list")
def list_snapshots(
    limit: int = typer.Option(20, "--limit", min=1, max=100, help="Max snapshots to list."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """List persisted 360log snapshots."""
    items = list_360log_snapshots(limit=limit)
    if json_output:
        _print_json({"ok": True, "items": items})
        return
    if not items:
        typer.echo("snapshots: (empty)")
        return
    for item in items:
        typer.echo(
            f"{item.get('snapshot_id')} "
            f"size={item.get('size_bytes')} "
            f"path={item.get('path')}"
        )


@app.command("show")
def show_snapshot(
    snapshot_id: str,
    timeline: bool = typer.Option(False, "--timeline", help="Print only normalized timeline items."),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON."),
) -> None:
    """Load a persisted 360log snapshot by id."""
    payload = load_360log_snapshot(snapshot_id)
    if timeline:
        payload = {"ok": True, "snapshot_id": snapshot_id, "timeline": payload.get("timeline")}
    if json_output:
        _print_json(payload)
        return
    typer.echo(f"snapshot_id={payload.get('snapshot_id')}")
    typer.echo(f"created_at={payload.get('created_at')}")
    timeline_payload = payload.get("timeline") if isinstance(payload.get("timeline"), dict) else {}
    typer.echo(f"items_total={timeline_payload.get('items_total') or 0}")
