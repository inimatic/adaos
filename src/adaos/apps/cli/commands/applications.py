"""Local owner lifecycle control, available even when all app UIs expired."""
import json
from typing import Optional

import typer

from adaos.services.agent_context import get_ctx


app = typer.Typer(help="Core-owned Application lifecycle (local OS-owner authority).")


def _owner():
    ctx = get_ctx()
    return getattr(ctx, "authority_context", None) or ctx


def _authority(capability):
    config = _owner().config
    subnet = str(config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id)
    return {"actor_ref": "cli:local-owner", "subnet_ref": subnet if subnet.startswith("subnet:") else "subnet:" + subnet,
            "capability": capability}


def _print(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command("show")
def show(application_id: str):
    from adaos.services.applications.lifecycle import ApplicationLifecycleService
    _print(ApplicationLifecycleService(_owner()).snapshot(application_id))


@app.command("trials")
def trials(application_id: str, offset: int = 0, limit: int = 20, storage: bool = False):
    from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
    _print(ApplicationLifecycleOperations(_owner()).trials(application_id, offset=offset, limit=limit, include_storage=storage))


@app.command("operations")
def operations(application_id: str, limit: int = 20):
    from adaos.services.applications.lifecycle import ApplicationLifecycleService
    _print(ApplicationLifecycleService(_owner()).operations(application_id, limit=limit))


@app.command("plan")
def plan(application_id: str, action: str, expected_revision: str, key: str,
         candidate_id: Optional[str] = None, acknowledge_write_loss: bool = False):
    """Save a plan only. Review its effects and exact digest before apply."""
    from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
    _print(ApplicationLifecycleOperations(_owner()).plan(application_id, action,
        expected_revision=expected_revision, idempotency_key=key, candidate_id=candidate_id,
        acknowledge_write_loss=acknowledge_write_loss, **_authority("applications.plan")))


@app.command("apply")
def apply(operation_id: str, plan_digest: str):
    from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
    _print(ApplicationLifecycleOperations(_owner()).apply(operation_id, plan_digest=plan_digest,
        **_authority("applications.apply")))


@app.command("cancel")
def cancel(operation_id: str, plan_digest: str):
    from adaos.services.applications.lifecycle_operations import cancel_planned_operation
    _print(cancel_planned_operation(_owner(), operation_id, plan_digest=plan_digest,
        **_authority("applications.plan")))


@app.command("recover")
def recover(operation_id: str, plan_digest: str):
    """Resume a CLI-owned exact intent. Never impersonate another actor."""
    from adaos.services.applications.operation_recovery import recover_core_operation
    _print(recover_core_operation(_owner(), operation_id, plan_digest=plan_digest,
        **_authority("applications.recover")))
