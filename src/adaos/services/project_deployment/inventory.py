from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from adaos.domain.project_deployment import (
    NodeEndpointRecord,
    NodeInventoryRecord,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _timestamp(value: Any) -> str:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()


def _integer_capacity(value: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, item in _mapping(value).items():
        if isinstance(item, bool):
            continue
        try:
            amount = int(item)
        except (TypeError, ValueError):
            continue
        if amount >= 0:
            result[str(key)] = amount
    return result


def _endpoint(value: Any) -> NodeEndpointRecord | None:
    raw = _mapping(value)
    endpoint_id = str(raw.get("endpoint_id") or raw.get("id") or "").strip()
    role = str(raw.get("role") or "").strip()
    if not endpoint_id or not role:
        return None
    return NodeEndpointRecord(
        endpoint_id=endpoint_id,
        role=role,
        available=raw.get("available") is True,
        capabilities=tuple(str(item) for item in list(raw.get("capabilities") or [])),
        labels={
            str(key): str(item) for key, item in _mapping(raw.get("labels")).items()
        },
        capacity=_integer_capacity(raw.get("capacity")),
    )


class SnapshotNodeInventoryProvider:
    """Adapt authenticated subnet link snapshots to the deployment inventory ABI."""

    def __init__(
        self,
        snapshot_provider: Callable[[], Mapping[str, Any]],
        *,
        local_records: Callable[[], Iterable[NodeInventoryRecord]] | None = None,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self.local_records = local_records

    def list_nodes(self, subnet_id: str) -> tuple[NodeInventoryRecord, ...]:
        records: dict[str, NodeInventoryRecord] = {}
        if self.local_records is not None:
            for record in self.local_records():
                if record.subnet_id == subnet_id:
                    records[record.node_id] = record
        snapshot = _mapping(self.snapshot_provider())
        for item in list(snapshot.get("members") or []):
            member = _mapping(item)
            node_snapshot = _mapping(member.get("node_snapshot"))
            deployment = _mapping(node_snapshot.get("deployment"))
            node_id = str(
                member.get("node_id") or node_snapshot.get("node_id") or ""
            ).strip()
            observed_subnet = str(node_snapshot.get("subnet_id") or subnet_id).strip()
            if not node_id or observed_subnet != subnet_id:
                continue
            build = _mapping(node_snapshot.get("build"))
            environment = _mapping(node_snapshot.get("environment"))
            endpoints = tuple(
                endpoint
                for endpoint in (
                    _endpoint(raw) for raw in list(deployment.get("endpoints") or [])
                )
                if endpoint is not None
            )
            connected = member.get("connected") is True
            ready = node_snapshot.get("ready") is True
            explicit_trust = str(member.get("trust_state") or "").strip().lower()
            trust_state = explicit_trust or ("trusted" if connected else "pending")
            labels = {
                str(key): str(value)
                for key, value in _mapping(deployment.get("labels")).items()
            }
            role = str(node_snapshot.get("role") or "").strip()
            if role:
                labels.setdefault("node.role", role)
            captured_at = node_snapshot.get("captured_at") or member.get("connected_at")
            try:
                revision = max(1, int(float(captured_at or 0) * 1000))
            except (TypeError, ValueError):
                revision = 1
            record = NodeInventoryRecord(
                node_id=node_id,
                subnet_id=observed_subnet,
                trust_state=trust_state,
                online=connected and ready,
                architecture=str(
                    deployment.get("architecture")
                    or environment.get("architecture")
                    or environment.get("platform")
                    or "unknown"
                ),
                runtime_version=str(
                    deployment.get("runtime_version")
                    or build.get("runtime_version")
                    or build.get("version")
                    or "unknown"
                ),
                capabilities=tuple(
                    str(value) for value in list(deployment.get("capabilities") or [])
                ),
                protocols={
                    str(key): str(value)
                    for key, value in _mapping(deployment.get("protocols")).items()
                },
                labels=labels,
                capacity=_integer_capacity(deployment.get("capacity")),
                endpoints=endpoints,
                observed_at=_timestamp(captured_at),
                revision=revision,
            )
            previous = records.get(node_id)
            if previous is None or record.revision >= previous.revision:
                records[node_id] = record
        return tuple(sorted(records.values(), key=lambda item: item.node_id))


__all__ = ["SnapshotNodeInventoryProvider"]
