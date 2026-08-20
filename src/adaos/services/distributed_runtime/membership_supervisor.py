from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from adaos.domain.distributed_runtime import ServiceEndpoint, ServiceInstance, utc_now
from adaos.services.agent_context import AgentContext

from .authorization import DistributedPrincipal
from .runtime import get_distributed_runtime


_LOG = logging.getLogger("adaos.distributed.membership")


def _utc(value: str) -> datetime:
    token = str(value or "").strip().replace("Z", "+00:00")
    observed = datetime.fromisoformat(token)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ServiceMembershipSpec:
    group_id: str
    lease_seconds: int
    protocol_version: str | None
    capabilities: tuple[str, ...]
    endpoints: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_mapping(
        cls,
        skill_name: str,
        value: Any,
    ) -> ServiceMembershipSpec | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("service.membership must be an object")
        allowed = {
            "enabled",
            "group_id",
            "lease_seconds",
            "protocol_version",
            "capabilities",
            "endpoints",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                "service.membership contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        if value.get("enabled") is not True:
            return None
        group_id = str(value.get("group_id") or "").strip()
        if not group_id:
            raise ValueError("service.membership.group_id is required")
        lease_seconds = max(30, min(int(value.get("lease_seconds") or 300), 600))
        protocol_version = str(value.get("protocol_version") or "").strip() or None
        raw_capabilities = value.get("capabilities") or []
        if not isinstance(raw_capabilities, list) or len(raw_capabilities) > 100:
            raise ValueError("service.membership.capabilities must be a bounded list")
        capabilities = tuple(
            sorted({str(item).strip() for item in raw_capabilities if str(item).strip()})
        )
        raw_endpoints = value.get("endpoints") or []
        if not isinstance(raw_endpoints, list) or not raw_endpoints or len(raw_endpoints) > 16:
            raise ValueError("service.membership.endpoints must contain 1..16 endpoints")
        endpoints: list[Mapping[str, Any]] = []
        required = {"endpoint_id", "protocol", "address_ref", "scopes", "metadata"}
        for raw in raw_endpoints:
            if not isinstance(raw, Mapping) or set(raw) != required:
                raise ValueError("service.membership endpoint fields are invalid")
            endpoint = dict(raw)
            address_ref = str(endpoint.get("address_ref") or "")
            allowed_templates = ("{node_id}", "{skill}", "{group_id}", "{activation_id}")
            remainder = address_ref
            for template in allowed_templates:
                remainder = remainder.replace(template, "")
            if "{" in remainder or "}" in remainder:
                raise ValueError("service.membership endpoint address_ref template is invalid")
            if "{skill}" not in address_ref and skill_name not in address_ref:
                raise ValueError("service.membership endpoint must identify the owning skill")
            endpoints.append(endpoint)
        return cls(
            group_id=group_id,
            lease_seconds=lease_seconds,
            protocol_version=protocol_version,
            capabilities=capabilities,
            endpoints=tuple(endpoints),
        )


class DistributedServiceMembershipSupervisor:
    """Reconcile service process health with exact Project-backed membership."""

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._next_expiry_reconcile_at = 0.0
        self._principal = DistributedPrincipal.create(
            actor_ref="core:service_membership_supervisor",
            permissions={
                "distributed.service.register",
                "distributed.service.renew",
                "distributed.service.reconcile",
            },
        )

    def status(self, skill_name: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._states.get(str(skill_name)) or {"enabled": False})

    def expire_stale(self) -> tuple[str, ...]:
        now = time.monotonic()
        if now < self._next_expiry_reconcile_at:
            return ()
        self._next_expiry_reconcile_at = now + 30.0
        try:
            expired = get_distributed_runtime().expire_leases(principal=self._principal)
        except Exception:
            _LOG.debug("distributed lease expiry reconcile deferred", exc_info=True)
            return ()
        if expired:
            _LOG.warning("expired distributed service leases count=%d", len(expired))
        return expired

    def reconcile(
        self,
        skill_name: str,
        spec: ServiceMembershipSpec,
        *,
        readiness: bool,
        health: Mapping[str, Any],
        pressure: Mapping[str, Any],
    ) -> dict[str, Any]:
        skill = str(skill_name or "").strip()
        try:
            receipt = self._reconcile(
                skill,
                spec,
                readiness=bool(readiness),
                health=dict(health),
                pressure=dict(pressure),
            )
        except Exception as exc:
            receipt = {
                "enabled": True,
                "ok": False,
                "state": "waiting",
                "group_id": spec.group_id,
                "error": f"{type(exc).__name__}: {exc}"[:500],
                "observed_at": utc_now(),
            }
            _LOG.warning(
                "distributed service membership reconcile failed skill=%s group=%s error=%s",
                skill,
                spec.group_id,
                receipt["error"],
            )
        with self._lock:
            self._states[skill] = dict(receipt)
        return receipt

    def _reconcile(
        self,
        skill_name: str,
        spec: ServiceMembershipSpec,
        *,
        readiness: bool,
        health: Mapping[str, Any],
        pressure: Mapping[str, Any],
    ) -> dict[str, Any]:
        runtime = get_distributed_runtime()
        node_id = str(self.ctx.config.node_id)
        component_ref = f"skill:{skill_name}"
        activation = self._selected_activation(
            runtime.deployment_store,
            node_id=node_id,
            component_ref=component_ref,
        )
        group = runtime.store.get_group(spec.group_id)
        definition = runtime.store.get_definition(
            group.definition_id,
            group.definition_version,
        )
        if activation.release_digest != definition.release_digest:
            raise RuntimeError("service_membership_release_not_defined")
        if spec.protocol_version and spec.protocol_version != definition.protocol_version:
            raise RuntimeError("service_membership_protocol_mismatch")

        instance_id = self._instance_id(spec.group_id, node_id, activation.activation_id)
        status = "ready" if readiness else "unavailable"
        endpoints = self._endpoints(
            spec,
            node_id=node_id,
            skill_name=skill_name,
            activation_id=activation.activation_id,
        )
        try:
            current = runtime.store.get_instance(instance_id)
        except FileNotFoundError:
            current = None
        if current is not None and current.status == "draining":
            return {
                "enabled": True,
                "ok": True,
                "state": "draining",
                "group_id": spec.group_id,
                "instance_id": instance_id,
                "revision": current.revision,
                "observed_at": utc_now(),
            }

        candidate = ServiceInstance(
            instance_id=instance_id,
            group_id=spec.group_id,
            node_id=node_id,
            activation_id=activation.activation_id,
            release_digest=activation.release_digest,
            component_ref=component_ref,
            runtime_generation=activation.generation,
            protocol_version=definition.protocol_version,
            topology_generation=group.desired_generation,
            lease_id=current.lease_id if current is not None else "membership-pending",
            status=status,
            readiness=readiness,
            health=dict(health),
            pressure=dict(pressure),
            capabilities=spec.capabilities,
            endpoints=endpoints,
            observed_at=utc_now(),
            revision=current.revision if current is not None else 1,
        )
        action = "current"
        if current is None:
            current = runtime.register_instance(
                candidate,
                expected_revision=0,
                lease_seconds=spec.lease_seconds,
                principal=self._principal,
            )
            action = "registered"
        else:
            lease = runtime.store.get_lease(current.lease_id)
            now = datetime.now(timezone.utc)
            lease_active = lease.status == "active" and _utc(lease.valid_until) > now
            changed = (
                current.readiness != readiness
                or current.status != status
                or dict(current.health) != dict(health)
                or dict(current.pressure) != dict(pressure)
            )
            renew_due = not lease_active or _utc(lease.renew_by) <= now
            if not lease_active:
                current = runtime.register_instance(
                    candidate,
                    expected_revision=current.revision,
                    lease_seconds=spec.lease_seconds,
                    principal=self._principal,
                )
                action = "registered"
            elif renew_due or changed:
                current = runtime.renew_instance(
                    instance_id,
                    expected_revision=current.revision,
                    readiness=readiness,
                    status=status,
                    health=health,
                    pressure=pressure,
                    lease_seconds=spec.lease_seconds,
                    principal=self._principal,
                )
                action = "renewed"
        lease = runtime.store.get_lease(current.lease_id)
        return {
            "enabled": True,
            "ok": True,
            "state": current.status,
            "action": action,
            "group_id": spec.group_id,
            "instance_id": current.instance_id,
            "activation_id": current.activation_id,
            "release_digest": current.release_digest,
            "runtime_generation": current.runtime_generation,
            "topology_generation": current.topology_generation,
            "revision": current.revision,
            "lease": {
                "lease_id": lease.lease_id,
                "status": lease.status,
                "renew_by": lease.renew_by,
                "valid_until": lease.valid_until,
            },
            "observed_at": utc_now(),
        }

    @staticmethod
    def _selected_activation(store: Any, *, node_id: str, component_ref: str) -> Any:
        cursor: str | None = None
        matches: list[Any] = []
        while True:
            values, cursor = store.list_activations(cursor=cursor, limit=100)
            matches.extend(
                item
                for item in values
                if item.node_id == node_id
                and item.component_ref == component_ref
                and item.status == "active"
            )
            if not cursor:
                break
        if not matches:
            raise RuntimeError("service_membership_activation_missing")
        return max(matches, key=lambda item: (item.generation, item.activation_id))

    @staticmethod
    def _instance_id(group_id: str, node_id: str, activation_id: str) -> str:
        digest = hashlib.sha256(
            f"{group_id}\0{node_id}\0{activation_id}".encode("utf-8")
        ).hexdigest()[:28]
        return f"service-{digest}"

    @staticmethod
    def _endpoints(
        spec: ServiceMembershipSpec,
        *,
        node_id: str,
        skill_name: str,
        activation_id: str,
    ) -> tuple[ServiceEndpoint, ...]:
        values: list[ServiceEndpoint] = []
        replacements = {
            "{node_id}": node_id,
            "{skill}": skill_name,
            "{group_id}": spec.group_id,
            "{activation_id}": activation_id,
        }
        for raw in spec.endpoints:
            payload = dict(raw)
            address_ref = str(payload.get("address_ref") or "")
            for pattern, replacement in replacements.items():
                address_ref = address_ref.replace(pattern, replacement)
            payload["address_ref"] = address_ref
            values.append(ServiceEndpoint.from_mapping(payload))
        return tuple(values)


__all__ = [
    "DistributedServiceMembershipSupervisor",
    "ServiceMembershipSpec",
]
