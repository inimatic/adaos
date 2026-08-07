from __future__ import annotations

import sys
import time
from dataclasses import dataclass

import pytest

from adaos.adapters.db.relational import SQLiteRelationalStorageProvider
from adaos.domain.execution import ExecutionResourceRequest, ExecutionSpec
from adaos.domain.provider_status import ProviderProtocolError
from adaos.services.execution.local import LocalProcessExecutor
from adaos.services.execution.workflow import ExecutionWorkflowActivityAdapter
from adaos.services.provider_status import build_provider_status_registry
from adaos.services.storage.relational import RelationalStorageBroker


@dataclass
class _Operation:
    operation_id: str


class _Operations:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []

    def create_operation(self, **kwargs):
        self.created.append(dict(kwargs))
        return _Operation("op.execution.1")

    def update_operation(self, operation_id: str, **kwargs):
        self.updated.append((operation_id, dict(kwargs)))
        return {"operation_id": operation_id, **kwargs}


def _wait(adapter, attempt_id: str, owner_ref: str, operation_id: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = adapter.reconcile_operation(
            attempt_id,
            owner_ref=owner_ref,
            operation_id=operation_id,
        )
        if result["status"] in {"succeeded", "failed", "cancelled", "lost"}:
            return result
        time.sleep(0.03)
    raise AssertionError("execution did not become terminal")


def test_provider_status_projection_and_protocol_negotiation(tmp_path) -> None:
    broker = RelationalStorageBroker((SQLiteRelationalStorageProvider(),))
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    registry = build_provider_status_registry(
        relational_broker=broker,
        executors=(executor,),
    )

    storage = registry.negotiate("storage.relational", "1.0")
    execution = registry.negotiate("execution.jobs", "1.0")
    assert storage.provider_id == "sqlite"
    assert "transactions" in storage.features
    assert execution.provider_id == "local-process"
    assert registry.projection()["healthy"] == 2
    with pytest.raises(ProviderProtocolError):
        registry.negotiate("execution.jobs", "2.0")


def test_workflow_activity_submits_executor_and_reuses_operation_manager(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    operations = _Operations()
    adapter = ExecutionWorkflowActivityAdapter(executor, operations)
    spec = ExecutionSpec(
        spec_id="workflow.fixture.v1",
        owner_ref="skill:research_manager_skill",
        command=(sys.executable, "-c", "print('workflow execution')"),
        working_directory=str(tmp_path),
        resources=ExecutionResourceRequest(wall_time_s=5),
    )

    submitted = adapter(
        {
            "attempt_id": "workflow-attempt-1",
            "effect_binding": {
                "execution_spec": spec.to_dict(),
                "execution_idempotency_key": "workflow-execution-1",
            },
        }
    )
    data = submitted["data"]
    assert submitted["outcome"] == "succeeded"
    assert data["operation_ref"] == {"kind": "operation", "id": "op.execution.1"}
    assert operations.created[0]["kind"] == "execution_attempt"

    terminal = _wait(
        adapter,
        data["execution_attempt"]["attempt_id"],
        spec.owner_ref,
        "op.execution.1",
    )
    assert terminal["status"] == "succeeded"
    assert operations.updated[-1][1]["finished"] is True
