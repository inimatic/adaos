from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from adaos.services.distributed_runtime import membership_supervisor as membership_module
from adaos.services.distributed_runtime.membership_supervisor import (
    DistributedServiceMembershipSupervisor,
    ServiceMembershipSpec,
)
from adaos.services.skill.service_supervisor import _resolve_service_spec


def _spec() -> ServiceMembershipSpec:
    value = ServiceMembershipSpec.from_mapping(
        "media_library_agent",
        {
            "enabled": True,
            "group_id": "media-library-home",
            "lease_seconds": 600,
            "protocol_version": "1",
            "capabilities": ["media.catalog"],
            "endpoints": [
                {
                    "endpoint_id": "catalog",
                    "protocol": "adaos.skill.v1",
                    "address_ref": "skill://{node_id}/{skill}/catalog",
                    "scopes": ["media.read"],
                    "metadata": {},
                }
            ],
        },
    )
    assert value is not None
    return value


class _Store:
    def __init__(self) -> None:
        self.instance = None
        self.lease = None

    def get_group(self, group_id: str):
        assert group_id == "media-library-home"
        return SimpleNamespace(
            group_id=group_id,
            definition_id="media-library-agent",
            definition_version="1",
            desired_generation=7,
        )

    def get_definition(self, definition_id: str, version: str):
        assert (definition_id, version) == ("media-library-agent", "1")
        return SimpleNamespace(release_digest="sha256:" + "a" * 64, protocol_version="1")

    def get_instance(self, instance_id: str):
        if self.instance is None or self.instance.instance_id != instance_id:
            raise FileNotFoundError(instance_id)
        return self.instance

    def get_lease(self, lease_id: str):
        assert self.lease is not None and self.lease.lease_id == lease_id
        return self.lease

    def list_instances(self, *, group_id=None, cursor=None, limit=100):
        assert cursor is None
        assert limit == 100
        values = () if self.instance is None else (self.instance,)
        if group_id is not None:
            values = tuple(item for item in values if item.group_id == group_id)
        return values, None


class _DeploymentStore:
    def __init__(self) -> None:
        self.generation = 9

    def list_activations(self, *, cursor=None, limit=100):
        assert cursor is None
        assert limit == 100
        return (
            (
                SimpleNamespace(
                    activation_id=(
                        "activation.media-agent-a"
                        if self.generation == 9
                        else f"activation.media-agent-a-v{self.generation}"
                    ),
                    component_ref="skill:media_library_agent",
                    node_id="node-a",
                    release_digest="sha256:" + "a" * 64,
                    generation=self.generation,
                    status="active",
                ),
                SimpleNamespace(
                    activation_id=(
                        "activation.media-agent-b"
                        if self.generation == 9
                        else f"activation.media-agent-b-v{self.generation}"
                    ),
                    component_ref="skill:media_library_agent",
                    node_id="node-b",
                    release_digest="sha256:" + "a" * 64,
                    generation=self.generation,
                    status="active",
                ),
            ),
            None,
        )


class _Runtime:
    def __init__(self) -> None:
        self.store = _Store()
        self.deployment_store = _DeploymentStore()
        self.register_calls = 0
        self.renew_calls = 0
        self.expire_calls = 0
        self.authority_renew_calls = 0

    def register_instance(self, instance, *, expected_revision, lease_seconds, principal):
        self.register_calls += 1
        assert expected_revision == (self.store.instance.revision if self.store.instance else 0)
        assert lease_seconds == 600
        principal.require("distributed.service.register")
        now = datetime.now(timezone.utc)
        self.store.lease = SimpleNamespace(
            lease_id=f"membership-{self.register_calls}",
            status="active",
            renew_by=(now + timedelta(seconds=400)).isoformat(),
            valid_until=(now + timedelta(seconds=600)).isoformat(),
        )
        self.store.instance = replace(
            instance,
            lease_id=self.store.lease.lease_id,
            revision=expected_revision + 1,
        )
        return self.store.instance

    def renew_instance(
        self,
        instance_id,
        *,
        expected_revision,
        readiness,
        status,
        health,
        pressure,
        lease_seconds,
        principal,
    ):
        self.renew_calls += 1
        principal.require("distributed.service.renew")
        current = self.store.get_instance(instance_id)
        assert expected_revision == current.revision
        assert lease_seconds == 600
        now = datetime.now(timezone.utc)
        self.store.lease.renew_by = (now + timedelta(seconds=400)).isoformat()
        self.store.lease.valid_until = (now + timedelta(seconds=600)).isoformat()
        self.store.instance = replace(
            current,
            readiness=readiness,
            status=status,
            health=dict(health),
            pressure=dict(pressure),
            revision=current.revision + 1,
        )
        return self.store.instance

    def expire_leases(self, *, principal):
        self.expire_calls += 1
        principal.require("distributed.service.reconcile")
        return ("old-membership",)

    def renew_authority_leases_for_instance(self, instance_id, *, principal):
        self.authority_renew_calls += 1
        principal.require("distributed.authority.renew")
        assert instance_id == self.store.instance.instance_id
        return ("authority-lease",)


def test_membership_supervisor_registers_and_renews_exact_activation(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(membership_module, "get_distributed_runtime", lambda: runtime)
    supervisor = DistributedServiceMembershipSupervisor(
        SimpleNamespace(config=SimpleNamespace(node_id="node-a"))
    )
    health = {"status": "passing", "ready": True, "http_status": 200}
    pressure = {"state": "normal", "active_jobs": 0}

    registered = supervisor.reconcile(
        "media_library_agent",
        _spec(),
        readiness=True,
        health=health,
        pressure=pressure,
    )
    current = supervisor.reconcile(
        "media_library_agent",
        _spec(),
        readiness=True,
        health=health,
        pressure=pressure,
    )
    runtime.store.lease.renew_by = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    renewed = supervisor.reconcile(
        "media_library_agent",
        _spec(),
        readiness=True,
        health=health,
        pressure=pressure,
    )

    assert registered["action"] == "registered"
    assert registered["runtime_generation"] == 9
    assert registered["topology_generation"] == 7
    assert current["action"] == "current"
    assert renewed["action"] == "renewed"
    assert runtime.register_calls == 1
    assert runtime.renew_calls == 1
    assert runtime.authority_renew_calls == 3
    assert renewed["renewed_authority_leases"] == ["authority-lease"]
    assert runtime.store.instance.endpoints[0].address_ref == (
        "skill://node-a/media_library_agent/catalog"
    )


def test_membership_supervisor_rolls_activation_under_stable_instance_identity(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(membership_module, "get_distributed_runtime", lambda: runtime)
    supervisor = DistributedServiceMembershipSupervisor(
        SimpleNamespace(config=SimpleNamespace(node_id="node-a"))
    )
    health = {"status": "passing", "ready": True, "http_status": 200}
    pressure = {"state": "normal", "active_jobs": 0}

    first = supervisor.reconcile(
        "media_library_agent",
        _spec(),
        readiness=True,
        health=health,
        pressure=pressure,
    )
    assert first["action"] == "registered"
    runtime.store.instance = replace(
        runtime.store.instance,
        instance_id="service-legacy-activation-bound",
    )
    first_instance_id = runtime.store.instance.instance_id
    runtime.deployment_store.generation = 10
    rolled = supervisor.reconcile(
        "media_library_agent",
        _spec(),
        readiness=True,
        health=health,
        pressure=pressure,
    )

    assert rolled["action"] == "registered"
    assert rolled["instance_id"] == first_instance_id
    assert rolled["activation_id"] == "activation.media-agent-a-v10"
    assert rolled["runtime_generation"] == 10
    assert runtime.register_calls == 2
    assert runtime.authority_renew_calls == 2


def test_membership_supervisor_reconciles_expired_leases_periodically(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(membership_module, "get_distributed_runtime", lambda: runtime)
    supervisor = DistributedServiceMembershipSupervisor(
        SimpleNamespace(config=SimpleNamespace(node_id="node-a"))
    )

    assert supervisor.expire_stale() == ("old-membership",)
    assert supervisor.expire_stale() == ()
    assert runtime.expire_calls == 1


def test_member_reports_membership_without_mutating_a_local_authority(monkeypatch) -> None:
    published = []

    class _Bus:
        def publish(self, event) -> None:
            published.append(event)

    monkeypatch.setattr(
        membership_module,
        "get_distributed_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("member must not open authority runtime")),
    )
    supervisor = DistributedServiceMembershipSupervisor(
        SimpleNamespace(
            config=SimpleNamespace(node_id="node-b", role="member"),
            bus=_Bus(),
        )
    )

    result = supervisor.reconcile(
        "media_library_agent",
        _spec(),
        readiness=True,
        health={"status": "passing"},
        pressure={"state": "normal"},
    )

    assert result["state"] == "reported"
    assert supervisor.expire_stale() == ()
    assert published[0].type == membership_module.MEMBERSHIP_REPORT_EVENT
    assert published[0].payload["membership"]["group_id"] == "media-library-home"


def test_authority_ingests_member_report_with_transport_node_identity(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(membership_module, "get_distributed_runtime", lambda: runtime)
    DistributedServiceMembershipSupervisor(
        SimpleNamespace(config=SimpleNamespace(node_id="node-a", role="hub"))
    )

    result = membership_module.ingest_remote_membership_report(
        node_id="node-b",
        payload={
            "skill": "media_library_agent",
            "membership": _spec().to_mapping(),
            "readiness": True,
            "health": {"status": "passing"},
            "pressure": {"state": "normal"},
            "node_id": "spoofed-node",
        },
    )

    assert result["action"] == "registered"
    assert runtime.store.instance.node_id == "node-b"
    assert runtime.store.instance.activation_id == "activation.media-agent-b"


def test_membership_manifest_rejects_implicit_or_unbounded_endpoints() -> None:
    assert ServiceMembershipSpec.from_mapping("demo", {"enabled": False}) is None
    with pytest.raises(ValueError, match="1..16"):
        ServiceMembershipSpec.from_mapping(
            "demo",
            {"enabled": True, "group_id": "group-a", "endpoints": []},
        )


def test_service_supervisor_resolves_membership_declaration(tmp_path) -> None:
    skill_root = tmp_path / "media_library_agent"
    skill_root.mkdir()
    spec = _resolve_service_spec(
        "media_library_agent",
        skill_root,
        {
            "runtime": {"kind": "service"},
            "service": {
                "port": 18106,
                "command": ["-m", "handlers.service"],
                "membership": {
                    "enabled": True,
                    "group_id": "media-library-home",
                    "lease_seconds": 600,
                    "endpoints": [
                        {
                            "endpoint_id": "catalog",
                            "protocol": "adaos.skill.v1",
                            "address_ref": "skill://{node_id}/{skill}/catalog",
                            "scopes": ["media.read"],
                            "metadata": {},
                        }
                    ],
                },
            },
        },
    )

    assert spec is not None
    assert spec.health_timeout_ms == 3000
    assert spec.startup_ready_timeout_s == 300.0
    assert spec.distributed_membership is not None
    assert spec.distributed_membership.group_id == "media-library-home"
