from __future__ import annotations

import inspect

from adaos.services.root_mcp import applications_plane
from adaos.services.root_mcp.policy import list_capability_classes
from adaos.services.root_mcp.registry import get_descriptor_set
from adaos.services.root_mcp.service import _execution_adapter_for_tool, list_tool_contracts, plane_registry
from adaos.services.root_mcp.sessions import DEFAULT_CAPABILITY_PROFILES


class _StubSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def list_applications(self, **kwargs):
        self.calls.append(("list_applications", (), kwargs))
        return [{"application": {"application_id": "app_recipes"}}]

    def plan_install(self, *args, **kwargs):
        self.calls.append(("plan_install", args, kwargs))
        return {"operation_id": "appop.1", "plan_digest": "sha256:" + "a" * 64}

    def apply_operation(self, *args, **kwargs):
        self.calls.append(("apply_operation", args, kwargs))
        return {"operation_id": args[0], "status": "succeeded"}

    def resolve_trial_link(self, *args, **kwargs):
        self.calls.append(("resolve_trial_link", args, kwargs))
        return {"application_id": "app_recipes", "release_digest": "sha256:" + "c" * 64}


def _context() -> dict:
    return {
        "actor": "user:owner",
        "scope": {"subnet_id": "sn_home", "zone": "local-dev"},
        "auth_context": {},
    }


def test_applications_plane_is_registered_with_bounded_contracts() -> None:
    plane = next(item for item in plane_registry()["planes"] if item["plane_id"] == "applications")
    contracts = list_tool_contracts(plane_id="applications")

    assert plane["title"] == "ApplicationsPlane"
    assert {item.id for item in contracts} == {
        "applications.list",
        "applications.show",
        "applications.list_releases",
        "applications.list_operations",
        "applications.poll_operation_events",
        "applications.get_operation",
        "applications.list_trial_access",
        "applications.list_development_reports",
        "applications.get_development_report_status",
        "applications.list_development_report_intakes",
        "applications.plan",
        "applications.apply",
        "applications.explain_plan",
        "applications.issue_trial_access",
        "applications.revoke_trial_access",
        "applications.resolve_trial_link",
        "applications.plan_trial_link_install",
    }
    forbidden = {"path", "command", "process", "git_credentials", "registry_path"}
    for contract in contracts:
        assert contract.metadata["adapter"] == "adaos.sdk.applications"
        assert forbidden.isdisjoint(contract.input_schema["properties"])


def test_applications_plane_forwards_mcp_actor_and_subnet_to_sdk(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)
    handlers = applications_plane.handlers()

    planned = handlers["applications.plan"](
        {
            "application_id": "app_recipes",
            "kind": "install",
            "release_digest": "sha256:" + "b" * 64,
            "expected_revision": 0,
            "idempotency_key": "install-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )
    applied = handlers["applications.apply"](
        {
            "operation_id": "appop.1",
            "plan_digest": planned["operation"]["plan_digest"],
            "idempotency_key": "install-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert applied["operation"]["status"] == "succeeded"
    assert stub.calls[0][2]["actor_ref"] == "user:owner"
    assert stub.calls[0][2]["subnet_ref"] == "subnet:sn_home"


def test_applications_plane_dry_run_does_not_call_sdk(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.plan"](
        {
            "application_id": "app_recipes",
            "kind": "remove",
            "expected_revision": 2,
            "idempotency_key": "remove-1",
        },
        dry_run=True,
    )

    assert result["would_plan"] is True
    assert stub.calls == []


def test_trial_link_redemption_uses_authenticated_subnet_and_zone(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.resolve_trial_link"](
        {
            "link": "adaos://applications/trial/grant-1?token=secret",
            "recipient_key_ref": "subnet-key:encryption-1",
            "redemption_id": "install-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["redemption"]["application_id"] == "app_recipes"
    assert stub.calls[0][2]["recipient_subnet_ref"] == "subnet:sn_home"
    assert stub.calls[0][2]["zone"] == "local-dev"


def test_applications_contract_descriptor_and_capability_profile_are_published() -> None:
    descriptor = get_descriptor_set("application_contracts")
    schemas = descriptor["payload"]["schemas"]
    capabilities = {item["capability"] for item in list_capability_classes()}

    assert len(schemas) == 7
    assert schemas["application.v1.schema.json"]["title"] == "AdaOS Application v1"
    assert {
        "applications.read", "applications.plan", "applications.apply",
        "applications.trial.install", "applications.publisher.read",
        "applications.develop", "applications.publish",
    } <= capabilities
    assert {
        "applications.read", "applications.plan", "applications.apply",
        "applications.trial.install", "applications.publisher.read",
    } <= set(
        DEFAULT_CAPABILITY_PROFILES["ApplicationsOperator"]
    )


def test_plane_handler_signatures_match_root_mcp_dispatch() -> None:
    for tool_id, handler in applications_plane.handlers().items():
        signature = inspect.signature(handler)
        assert list(signature.parameters) == ["arguments", "dry_run"]
        assert _execution_adapter_for_tool(tool_id) == "sdk.applications"
