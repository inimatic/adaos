"""Initialize or verify the current skill's declared SQLite schema, never switch channels."""

import asyncio
from pathlib import Path

import yaml

from adaos.sdk.core._ctx import require_ctx
from adaos.services.applications.data_lifecycle import declared_databases
from adaos.services.applications.sqlite_data_transition import initialize_sqlite_schema
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.skill.data_paths import resolve_skill_data_root


def ensure_database(path: str) -> dict:
    """Use the pinned skill.yaml data_lifecycle chain and Core checksum ledger.

    Requires storage.relational. An empty store is initialized; an existing
    installed store must already match the complete chain. Pending installed
    migrations require Core's fenced Beta cutover. DEV-only synthetic stores may
    migrate in place. No path, SQL or production data can be supplied as a bypass.
    Call before opening the declared database with sqlite3; never maintain a
    separate migration ledger or duplicate the chain in handlers.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Database initialization performs local I/O; use a_ensure_database from async handlers")
    ctx = require_ctx("sdk.data.lifecycle.ensure_database")
    admitted = require_skill_capability(ctx, "storage.relational")
    current = ctx.skill_ctx.get()
    manifest = yaml.safe_load(Path(admitted.manifest_path).read_text(encoding="utf-8"))
    databases = declared_databases(manifest)
    if path not in databases:
        raise ValueError("Database is not declared in this skill's data_lifecycle")
    root = resolve_skill_data_root(ctx, current).resolve()
    destination = root / path
    if destination.resolve() != destination or not destination.is_relative_to(root):
        raise ValueError("Database path escaped the current skill's data root or used a link")
    source = Path(current.path).resolve()
    trial = str(getattr(ctx.paths, "runtime_channel_ref", "workspace")).startswith("trial:")
    getter = None if trial else getattr(ctx.paths, "dev_skills_dir", None)
    dev = Path(getter()).resolve() if getter else None
    development = bool(dev and (source.is_relative_to(dev / current.name)
                               or source.is_relative_to(dev / ".runtime" / current.name)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    return {"path": path, **initialize_sqlite_schema(destination, databases[path], development=development)}


async def a_ensure_database(path: str) -> dict:
    """Async ensure_database preserving current skill/caller context."""
    return await asyncio.to_thread(ensure_database, path)


__all__ = ["ensure_database", "a_ensure_database"]
