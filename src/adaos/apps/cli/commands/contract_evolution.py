"""Local operator tools for Contract Evolution, independent of GitHub CI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from adaos.services.contract_evolution.service import ContractEvolutionService, WAVE


app = typer.Typer(help="Compare contracts, assess consumers and inspect compatibility work.")


def _service(state_dir: Optional[Path]):
    if state_dir is None:
        from adaos.services.agent_context import get_ctx
        ctx = get_ctx()
        state_dir = Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir())
    return ContractEvolutionService(state_dir)


def _print(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _snapshot():
    from adaos.sdk.core.exporter import export
    from adaos.services.interface_deprecations import _CATALOG_PATH
    return {"schema": "adaos.contract_snapshot.v1", "sdk": export(level="rich", include_deprecated=True),
            "ui": json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))}


@app.command("snapshot")
def snapshot(output: Path = typer.Option(..., "--output")):
    """Export the current trusted publisher contracts for a later comparison."""
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    atomic_write_json(output, _snapshot())
    _print({"output": str(output)})


@app.command("compare")
def compare(baseline: Path = typer.Option(..., "--baseline"),
            target: Optional[Path] = typer.Option(None, "--target"),
            state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    """Publish exact SDK/UI changes to local evolution state; no consumer mutation."""
    from adaos.services.contract_evolution.producers import publish_sdk_changes, publish_ui_changes
    old = json.loads(baseline.read_text(encoding="utf-8"))
    new = json.loads(target.read_text(encoding="utf-8")) if target else _snapshot()
    service = _service(state_dir)
    changes = [*publish_sdk_changes(service, old["sdk"], new["sdk"]),
               *publish_ui_changes(service, old["ui"], new["ui"])]
    _print({"changes": changes, "count": len(changes)})


@app.command("scan-source")
def scan_source(source: Path = typer.Argument(...),
                application_ref: str = typer.Option(..., "--application"),
                consumer_ref: str = typer.Option(..., "--consumer"),
                state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    """Index a source snapshot without importing or activating its handlers."""
    from adaos.services.contract_evolution.usage import build_usage_lock
    from adaos.services.artifact_pipeline.packages import artifact_source_snapshot
    snapshot = artifact_source_snapshot(source)
    files = {entry["path"]: (source / entry["path"]).read_bytes() for entry in snapshot["files"]}
    entry = build_usage_lock(files, application_ref=application_ref, consumer_ref=consumer_ref,
                             consumer_digest=snapshot["digest"])
    _print(_service(state_dir).register_usage(entry))


@app.command("index-releases")
def index_releases(state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    """Rebuild usage evidence for current admitted Application releases."""
    from adaos.services.applications.store import ApplicationStore
    service = _service(state_dir)
    store = ApplicationStore(service.state_dir)
    rows = []
    for application in store.list_applications():
        channels = store.get_channels(application.application_id).get("channels", {})
        for release_digest in sorted(set(channels.values())):
            release = store.get_release(application.application_id, release_digest)
            usage = service.register_application_release(release)
            rows.append({"application_id": application.application_id, "release_digest": release_digest,
                         "usage_lock_id": usage["usage_lock_id"] if usage else None,
                         "status": "indexed" if usage else "legacy_usage_unknown"})
    _print({"items": rows})


@app.command("reconcile")
def reconcile(materialize: bool = typer.Option(False, "--materialize-tickets"),
              state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    """Assess registered consumers; optionally materialize only actionable Tickets."""
    _print(_service(state_dir).reconcile(materialize=materialize))


@app.command("show")
def show(application_ref: str = typer.Argument(...),
         consumer_digest: Optional[str] = typer.Option(None, "--consumer-digest"),
         state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    _print(_service(state_dir).application_projection(application_ref, consumer_digest=consumer_digest))


@app.command("waves")
def waves(state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    service = _service(state_dir)
    _print({"items": [service.wave_projection(item["wave_id"]) for item in service.store.list(WAVE, limit=100)]})


@app.command("events")
def events(after: int = typer.Option(0, "--after"),
           state_dir: Optional[Path] = typer.Option(None, "--state-dir")):
    _print(_service(state_dir).store.events(after=after))
