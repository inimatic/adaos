from __future__ import annotations

import inspect

from adaos.services.root_mcp import applications_plane
from adaos.services.root_mcp.model import RootMcpResponseEnvelope, RootMcpSurface
from adaos.services.root_mcp.policy import list_capability_classes
from adaos.services.root_mcp.registry import get_descriptor_set
from adaos.services.root_mcp.service import (
    _execution_adapter_for_tool,
    list_tool_contracts,
    plane_registry,
)
from adaos.services.root_mcp.sessions import DEFAULT_CAPABILITY_PROFILES


class _StubSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def list_applications(self, **kwargs):
        self.calls.append(("list_applications", (), kwargs))
        return [{"application": {"application_id": "app_recipes"}}]

    def list_application_components(self, *args, **kwargs):
        self.calls.append(("list_application_components", args, kwargs))
        return [{"component_ref": "scenario:recipes", "ownership": "owned"}]

    def list_application_placements(self, *args, **kwargs):
        self.calls.append(("list_application_placements", args, kwargs))
        return [
            {
                "placement_id": "scenario:recipes@node-home",
                "component_ref": "scenario:recipes",
                "node_id": "node-home",
                "sync_status": "synced",
            }
        ]

    def get_application_placement_options(self, *args, **kwargs):
        self.calls.append(("get_application_placement_options", args, kwargs))
        return {
            "schema": "adaos.application.placement_options.v1",
            "application_id": args[0],
            "deployment_id": "application-deployment:app_recipes",
            "expected_revision": 4,
            "component_ref": kwargs.get("component_ref"),
            "placements": [
                {
                    "placement_id": "scenario:recipes@node-home",
                    "component_ref": "scenario:recipes",
                    "node_id": "node-home",
                    "desired": True,
                    "desired_mode": "selected_nodes",
                    "runtime_status": "active",
                    "sync_status": "synced",
                    "deployment_revision": 4,
                }
            ],
            "eligible_nodes": [
                {
                    "node_id": "node-office",
                    "score": 10,
                    "already_active": False,
                    "architecture": "x86_64",
                    "runtime_version": "1.0.0",
                    "labels": {},
                    "headroom": {},
                    "reasons": ["eligible"],
                }
            ],
            "rejected_nodes": [],
            "truncated": False,
        }

    def get_application_setup(self, *args, **kwargs):
        self.calls.append(("get_application_setup", args, kwargs))
        return {
            "schema": "adaos.application.setup_surface.v1",
            "available": True,
            "application_id": args[0],
            "release_digest": kwargs.get("release_digest"),
            "reason": None,
            "contract": {},
            "state": {},
            "configuration": [],
            "provider_configuration": [],
            "editors": {"settings": [], "credentials": [], "providers": []},
        }

    def update_application_configuration(self, *args, **kwargs):
        self.calls.append(("update_application_configuration", args, kwargs))
        return {"configuration": {"revision": kwargs["expected_revision"] + 1}}

    def update_application_credential(self, *args, **kwargs):
        self.calls.append(("update_application_credential", args, kwargs))
        return {
            "credential": {
                "slot": args[2],
                "present": args[3] is not None,
                "revision": kwargs["expected_revision"] + 1,
            }
        }

    def assess_updates(self, **kwargs):
        self.calls.append(("assess_updates", (), kwargs))
        return {"eligible_count": 1, "items": [{"application_id": "app_recipes"}]}

    def plan_available_updates(self, **kwargs):
        self.calls.append(("plan_available_updates", (), kwargs))
        return {
            "batch_id": "appbatch." + "a" * 32,
            "plan_digest": "sha256:" + "b" * 64,
            "status": "planned",
        }

    def get_update_batch(self, batch_id):
        self.calls.append(("get_update_batch", (batch_id,), {}))
        return {"batch_id": batch_id, "status": "planned"}

    def apply_update_batch(self, *args, **kwargs):
        self.calls.append(("apply_update_batch", args, kwargs))
        return {"batch_id": args[0], "status": "succeeded"}

    def plan_install(self, *args, **kwargs):
        self.calls.append(("plan_install", args, kwargs))
        return {"operation_id": "appop.1", "plan_digest": "sha256:" + "a" * 64}

    def plan_relocate_component(self, *args, **kwargs):
        self.calls.append(("plan_relocate_component", args, kwargs))
        return {
            "operation_id": "appop.relocate",
            "plan_digest": "sha256:" + "e" * 64,
        }

    def plan_install_component(self, *args, **kwargs):
        self.calls.append(("plan_install_component", args, kwargs))
        return {
            "operation_id": "appop.install-component",
            "plan_digest": "sha256:" + "c" * 64,
        }

    def plan_remove_component(self, *args, **kwargs):
        self.calls.append(("plan_remove_component", args, kwargs))
        return {
            "operation_id": "appop.remove-component",
            "plan_digest": "sha256:" + "f" * 64,
        }

    def apply_operation(self, *args, **kwargs):
        self.calls.append(("apply_operation", args, kwargs))
        return {"operation_id": args[0], "status": "succeeded"}

    def plan_update_track(self, *args, **kwargs):
        self.calls.append(("plan_update_track", args, kwargs))
        return {
            "operation_id": "appop.settings",
            "plan_digest": "sha256:" + "d" * 64,
            "idempotency_key": kwargs["idempotency_key"],
        }

    def set_home_pinned(self, *args, **kwargs):
        self.calls.append(("set_home_pinned", args, kwargs))
        return {
            "application_id": args[0],
            "webspace_id": kwargs["webspace_id"],
            "pinned": kwargs["pinned"],
        }

    def reorder_home_application(self, *args, **kwargs):
        self.calls.append(("reorder_home_application", args, kwargs))
        return {
            "application_id": args[0],
            "webspace_id": kwargs["webspace_id"],
            "home_order": kwargs["to_index"],
        }

    def resolve_trial_link(self, *args, **kwargs):
        self.calls.append(("resolve_trial_link", args, kwargs))
        return {"application_id": "app_recipes", "release_digest": "sha256:" + "c" * 64}

    def submit_development_report(self, *args, **kwargs):
        self.calls.append(("submit_development_report", args, kwargs))
        return {"report": {"report_id": "report.1"}, "duplicate": False}

    def get_application_access_surface(self, *args, **kwargs):
        self.calls.append(("get_application_access_surface", args, kwargs))
        return {
            "schema": "adaos.application.access_surface.v1",
            "application": {"application_id": args[0]},
            "release": {"release_digest": kwargs.get("release_digest")},
            "installation": None,
            "sections": {
                "permissions": {},
                "access": [],
                "roles": [],
                "connected_accounts": [],
                "release_readiness": None,
                "activity": [],
                "activity_page": {
                    "limit": kwargs.get("activity_limit", 50),
                    "has_more": False,
                },
            },
        }

    def update_provider_configuration(self, *args, **kwargs):
        self.calls.append(("update_provider_configuration", args, kwargs))
        return {
            "provider_configuration": {
                "provider_id": args[1],
                "status": "ready",
                "revision": kwargs["expected_revision"] + 1,
                "present_fields": sorted(args[2]),
            }
        }

    def get_users_access_surface(self, *args, **kwargs):
        self.calls.append(("get_users_access_surface", args, kwargs))
        return {
            "schema": "adaos.users_access.surface.v1",
            "people": [],
            "guests": [],
            "children": [],
            "subjects": [],
            "devices": [],
            "sessions": [],
            "application_access": [],
            "permissions": [],
            "activity": [],
            "activity_page": {"limit": kwargs.get("activity_limit", 50), "has_more": False},
            "diagnostics": {"content_redacted": True},
        }

    def put_application_connected_account(self, *args, **kwargs):
        self.calls.append(("put_application_connected_account", args, kwargs))
        return {"account_id": "calendar-user", "status": "connected"}


class _StubBuilderSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def create_application(self, *args, **kwargs):
        self.calls.append(("create_application", args, kwargs))
        return {"operation_id": "appdevop.1", "status": "succeeded"}

    def update_application_metadata(self, *args, **kwargs):
        self.calls.append(("update_application_metadata", args, kwargs))
        return {"operation_id": "appdevop.2", "status": "succeeded"}

    def delete_application_development(self, *args, **kwargs):
        self.calls.append(("delete_application_development", args, kwargs))
        return {"operation_id": "appdevop.3", "status": "succeeded"}

    def create_trial(self, *args, **kwargs):
        self.calls.append(("create_trial", args, kwargs))
        return {"operation_id": "appdevop.trial", "status": "succeeded"}

    def publish_to_registry(self, *args, **kwargs):
        self.calls.append(("publish_to_registry", args, kwargs))
        return {"operation_id": "appdevop.4", "status": "succeeded"}

    def reconcile_development_operation(self, *args, **kwargs):
        self.calls.append(("reconcile_development_operation", args, kwargs))
        return {"operation_id": args[0], "status": "succeeded"}


def _context() -> dict:
    return {
        "actor": "user:owner",
        "scope": {"subnet_id": "sn_home", "zone": "local-dev"},
        "auth_context": {},
    }


def test_applications_plane_is_registered_with_bounded_contracts() -> None:
    plane = next(
        item
        for item in plane_registry()["planes"]
        if item["plane_id"] == "applications"
    )
    contracts = list_tool_contracts(plane_id="applications")

    assert plane["title"] == "ApplicationsPlane"
    assert {item.id for item in contracts} == {
        "applications.list",
        "applications.show",
        "applications.assess_updates",
        "applications.plan_updates",
        "applications.get_update_batch",
        "applications.apply_updates",
        "applications.list_components",
        "applications.list_placements",
        "applications.setup.show",
        "applications.setup.configure",
        "applications.setup.credential",
        "applications.setup.provider",
        "applications.set_home_pin",
        "applications.reorder_home",
        "applications.update_settings",
        "applications.access.show",
        "applications.access.users",
        "applications.access.reviews",
        "applications.access.privacy",
        "applications.access.simulate",
        "applications.access.grant",
        "applications.access.change",
        "applications.access.revoke",
        "applications.access.export",
        "applications.access.import",
        "applications.access.connected_account",
        "applications.access.update_review",
        "applications.access.profile",
        "applications.access.verify_release",
        "applications.list_releases",
        "applications.list_operations",
        "applications.poll_operation_events",
        "applications.get_operation",
        "applications.list_trial_access",
        "applications.get_prerelease_rollout",
        "applications.list_development_reports",
        "applications.get_development_report_status",
        "applications.list_development_report_intakes",
        "applications.list_development_report_appeals",
        "applications.list_publisher_development_report_appeals",
        "applications.get_development_report_triage",
        "applications.submit_development_report",
        "applications.sync_development_reports",
        "applications.triage_development_report",
        "applications.accept_development_report",
        "applications.set_development_report_status",
        "applications.submit_development_report_appeal",
        "applications.resolve_development_report_appeal",
        "applications.verify_development_report_release",
        "applications.request_development_report_resync",
        "applications.development.list_operations",
        "applications.development.get_operation",
        "applications.development.reconcile_operation",
        "applications.development.create",
        "applications.development.update_metadata",
        "applications.development.delete",
        "applications.development.materialize",
        "applications.development.preview",
        "applications.development.create_trial",
        "applications.development.decide_trial",
        "applications.development.publish_link_trial",
        "applications.development.publish_prerelease",
        "applications.development.promote_stable",
        "applications.development.publish_to_registry",
        "applications.development.publish_stable_source",
        "applications.plan",
        "applications.apply",
        "applications.explain_plan",
        "applications.issue_trial_access",
        "applications.revoke_trial_access",
        "applications.resolve_trial_link",
        "applications.plan_trial_link_install",
        "applications.set_prerelease_rollout",
        "applications.record_prerelease_health",
    }
    forbidden = {"path", "command", "process", "git_credentials", "registry_path"}
    for contract in contracts:
        expected_adapter = (
            "adaos.sdk.builder.applications"
            if contract.id.startswith("applications.development.")
            else "adaos.sdk.applications"
        )
        assert contract.metadata["adapter"] == expected_adapter
        assert forbidden.isdisjoint(contract.input_schema["properties"])


def test_root_mcp_response_preserves_empty_application_collections() -> None:
    response = RootMcpResponseEnvelope(
        request_id="request.empty-applications",
        trace_id="trace.empty-applications",
        tool_id="applications.list",
        surface=RootMcpSurface.OPERATIONS,
        ok=True,
        status="ok",
        result={"applications": [], "page": {}},
    )

    assert response.to_dict()["result"] == {"applications": [], "page": {}}


def test_application_catalog_contracts_publish_exact_records_and_cas_paths() -> None:
    contracts = {item.id: item for item in applications_plane.contracts()}
    listed = contracts["applications.list"]
    shown = contracts["applications.show"]

    list_result = listed.output_schema["properties"]["result"]
    record = list_result["properties"]["applications"]["items"]
    assert list_result["required"] == ["applications"]
    assert {
        "application",
        "installed",
        "available",
        "installation",
        "subscription",
        "effective_release",
        "effective_navigation",
        "attention",
    }.issubset(record["properties"])
    assert record["properties"]["application"]["required"] == ["application_id"]
    installation = record["properties"]["installation"]["oneOf"][1]
    assert installation["required"] == ["revision"]
    development = record["properties"]["local_development"]["oneOf"][1]
    trial = development["properties"]["trial"]
    assert "release_digest" in trial["required"]
    target = trial["properties"]["navigation_target"]["oneOf"][1]
    assert target["required"] == [
        "intent",
        "expected_scenario_id",
        "webspace_id",
        "space_kind",
        "candidate_id",
        "candidate_digest",
    ]
    assert development["properties"]["publication"]["required"] == [
        "status",
        "evidence_present",
    ]
    effective_navigation = record["properties"]["effective_navigation"]
    assert effective_navigation["required"] == [
        "schema",
        "status",
        "reason",
        "target",
    ]
    installed_target = effective_navigation["properties"]["target"]["oneOf"][1]
    assert installed_target["required"] == [
        "intent",
        "expected_scenario_id",
        "webspace_id",
        "space_kind",
        "application_id",
        "release_digest",
    ]

    shown_record = shown.output_schema["properties"]["result"]["properties"][
        "application"
    ]
    assert shown_record == record
    assert listed.metadata["webui_data_binding"]["result_paths"] == {
        "records": "response.result.applications"
    }
    assert shown.metadata["webui_data_binding"]["result_paths"] == {
        "record": "response.result.application"
    }
    assert shown.metadata["webui_data_binding"]["record"] == {
        "identity_path": "application.application_id",
        "application_revision_path": "application.revision",
        "installation_revision_path": "installation.revision",
        "subscription_revision_path": "subscription.revision",
        "effective_release_digest_path": "effective_release.release_digest",
        "accepted_trial_path": "local_development.trial",
        "trial_navigation_target_path": (
            "local_development.trial.navigation_target"
        ),
        "effective_navigation_path": "effective_navigation",
        "effective_navigation_target_path": "effective_navigation.target",
        "publication_evidence_path": "local_development.publication",
    }
    assert "effective_release" in shown.metadata["webui_data_binding"][
        "unavailable_state"
    ]["nullable_paths"]
    assert shown.metadata["webui_data_binding"]["not_found"] == {
        "transport_status": "error",
        "error_type": "FileNotFoundError",
    }


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


def test_applications_plane_forwards_catalog_and_development_filters(
    monkeypatch,
) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    applications_plane.handlers()["applications.list"](
        {
            "installed_only": False,
            "catalog_only": True,
            "available_only": True,
            "developed_only": True,
        },
        dry_run=True,
    )

    assert stub.calls == [
        (
            "list_applications",
            (),
            {
                "installed_only": False,
                "catalog_only": True,
                "available_only": True,
                "developed_only": True,
                "webspace_id": "desktop",
            },
        )
    ]


def test_applications_plane_exposes_reviewed_bulk_update_flow(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)
    handlers = applications_plane.handlers()

    assessment = handlers["applications.assess_updates"](
        {"webspace_id": "desktop"}, dry_run=False
    )
    planned = handlers["applications.plan_updates"](
        {
            "application_ids": ["app_recipes"],
            "idempotency_key": "bulk-plan-1",
            "webspace_id": "desktop",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )
    applied = handlers["applications.apply_updates"](
        {
            "batch_id": planned["batch"]["batch_id"],
            "plan_digest": planned["batch"]["plan_digest"],
            "idempotency_key": "bulk-apply-1",
            "webspace_id": "desktop",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert assessment["assessment"]["eligible_count"] == 1
    assert applied["batch"]["status"] == "succeeded"
    assert stub.calls[1][2]["actor_ref"] == "user:owner"
    assert stub.calls[1][2]["subnet_ref"] == "subnet:sn_home"
    assert stub.calls[2][2]["capability"] == "applications.apply"


def test_applications_plane_lists_component_inventory(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.list_components"](
        {
            "application_id": "app_recipes",
            "webspace_id": "home",
        },
        dry_run=True,
    )

    assert result["components"][0]["component_ref"] == "scenario:recipes"
    assert result["application_id"] == "app_recipes"
    assert result["deployment_revision"] is None
    contract = {item.id: item for item in applications_plane.contracts()}[
        "applications.list_components"
    ]
    result_schema = contract.output_schema["properties"]["result"]
    component_schema = result_schema["properties"]["components"]["items"]
    assert {"installable", "relocatable", "removable", "deployment_revision"}.issubset(
        component_schema["properties"]
    )
    assert stub.calls == [
        (
            "list_application_components",
            ("app_recipes",),
            {"webspace_id": "home"},
        )
    ]


def test_applications_plane_lists_desired_and_observed_placements(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.list_placements"](
        {
            "application_id": "app_recipes",
            "component_ref": "scenario:recipes",
            "limit": 20,
            "webspace_id": "home",
        },
        dry_run=True,
    )

    assert result["placements"][0]["sync_status"] == "synced"
    assert result["expected_revision"] == 4
    assert result["eligible_nodes"][0]["node_id"] == "node-office"
    contract = {item.id: item for item in applications_plane.contracts()}[
        "applications.list_placements"
    ]
    result_schema = contract.output_schema["properties"]["result"]
    assert {"expected_revision", "eligible_nodes", "placements"}.issubset(
        result_schema["properties"]
    )
    assert stub.calls == [
        (
            "get_application_placement_options",
            ("app_recipes",),
            {
                "component_ref": "scenario:recipes",
                "webspace_id": "home",
                "limit": 20,
            },
        )
    ]


def test_applications_plane_exposes_release_owned_setup_without_secret_echo(
    monkeypatch,
) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)
    digest = "sha256:" + "a" * 64
    handlers = applications_plane.handlers()

    shown = handlers["applications.setup.show"](
        {
            "application_id": "app_recipes",
            "release_digest": digest,
            "webspace_id": "desktop",
        },
        dry_run=True,
    )
    configured = handlers["applications.setup.configure"](
        {
            "application_id": "app_recipes",
            "release_digest": digest,
            "component_ref": "skill:recipes",
            "values": {"theme": "dark"},
            "expected_revision": 2,
            "webspace_id": "desktop",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )
    credential = handlers["applications.setup.credential"](
        {
            "application_id": "app_recipes",
            "release_digest": digest,
            "component_ref": "skill:recipes",
            "slot": "api_token",
            "value": "secret-value",
            "expected_revision": 3,
            "webspace_id": "desktop",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )
    provider = handlers["applications.setup.provider"](
        {
            "application_id": "app_recipes",
            "release_digest": digest,
            "provider_id": "google.gmail",
            "values": {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            "expected_revision": 4,
            "webspace_id": "desktop",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert shown["setup"]["available"] is True
    contract = {item.id: item for item in applications_plane.contracts()}[
        "applications.setup.show"
    ]
    setup_schema = contract.output_schema["properties"]["result"]["properties"]["setup"]
    assert setup_schema["properties"]["editors"]["properties"]["settings"]["items"][
        "properties"
    ]["fields"]["items"]["required"] == [
        "id",
        "type",
        "label",
        "required",
    ]
    assert configured["configuration"]["revision"] == 3
    assert credential["credential"] == {
        "slot": "api_token",
        "present": True,
        "revision": 4,
    }
    assert "secret-value" not in repr(credential)
    assert provider["provider_configuration"] == {
        "provider_id": "google.gmail",
        "status": "ready",
        "revision": 5,
        "present_fields": ["client_id", "client_secret"],
    }
    assert "client-secret" not in repr(provider)
    assert stub.calls[-1][0] == "update_provider_configuration"


def test_applications_plane_exposes_explicit_home_pin_mutation(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.set_home_pin"](
        {
            "application_id": "app_recipes",
            "pinned": False,
            "webspace_id": "family",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["home"] == {
        "application_id": "app_recipes",
        "webspace_id": "family",
        "pinned": False,
    }
    assert stub.calls == [
        (
            "set_home_pinned",
            ("app_recipes",),
            {"pinned": False, "webspace_id": "family"},
        )
    ]


def test_applications_plane_exposes_authoritative_home_reorder(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.reorder_home"](
        {
            "application_id": "app_recipes",
            "to_index": 4,
            "webspace_id": "family",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["home"] == {
        "application_id": "app_recipes",
        "webspace_id": "family",
        "home_order": 4,
    }
    assert stub.calls == [
        (
            "reorder_home_application",
            ("app_recipes",),
            {"to_index": 4, "webspace_id": "family"},
        )
    ]


def test_applications_plane_updates_preferences_in_one_atomic_command(
    monkeypatch,
) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.update_settings"](
        {
            "application_id": "app_recipes",
            "auto_update_enabled": False,
            "use_prerelease": True,
            "expected_revision": 3,
            "idempotency_key": "settings-1",
            "webspace_id": "desktop",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["operation"]["status"] == "succeeded"
    assert result["settings"] == {
        "auto_update_enabled": False,
        "use_prerelease": True,
        "paused": False,
    }
    assert stub.calls == [
        (
            "plan_update_track",
            ("app_recipes",),
            {
                "update_track": "prerelease",
                "update_policy": "notify",
                "paused": False,
                "expected_revision": 3,
                "actor_ref": "user:owner",
                "subnet_ref": "subnet:sn_home",
                "capability": "applications.plan",
                "idempotency_key": "settings-1",
            },
        ),
        (
            "apply_operation",
            ("appop.settings",),
            {
                "plan_digest": "sha256:" + "d" * 64,
                "actor_ref": "user:owner",
                "subnet_ref": "subnet:sn_home",
                "capability": "applications.apply",
                "idempotency_key": "settings-1",
                "webspace_id": "desktop",
            },
        ),
    ]


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


def test_application_access_contracts_are_secret_free_and_reads_share_sdk_projection(
    monkeypatch,
) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)
    contracts = {item.id: item for item in applications_plane.contracts()}
    connected = contracts["applications.access.connected_account"]
    access_show = contracts["applications.access.show"]
    users = contracts["applications.access.users"]

    assert {"secret", "token", "credential", "value"}.isdisjoint(
        connected.input_schema["properties"]
    )
    assert "expected_revision" in connected.input_schema["required"]
    assert connected.input_schema["properties"]["expected_revision"]["minimum"] == 0
    accounts = access_show.output_schema["properties"]["result"]["properties"][
        "access"
    ]["properties"]["sections"]["properties"]["connected_accounts"]
    sections = access_show.output_schema["properties"]["result"]["properties"][
        "access"
    ]["properties"]["sections"]["properties"]
    assert sections["access"]["items"]["required"] == [
        "schema",
        "grant_id",
        "subject_ref",
        "application_id",
        "application_roles",
        "permission_ceiling",
        "explicit_denies",
        "constraints",
        "issuer_ref",
        "reviewed_permission_profile_digest",
        "status",
        "expires_at",
        "revision",
        "created_at",
        "updated_at",
    ]
    assert sections["roles"]["items"]["required"] == [
        "id",
        "title",
        "grants",
        "assignable_to",
        "default_for",
        "requires_permissions",
    ]
    profile = sections["permissions"]["properties"]["profile"]
    assert profile["properties"]["required"]["items"]["required"] == [
        "id",
        "purpose",
        "approval_policy",
    ]
    assert accounts["items"]["required"] == [
        "account_id",
        "provider_id",
        "subject_ref",
        "mode",
        "scopes",
        "status",
        "revision",
    ]
    assert access_show.metadata["webui_data_binding"][
        "connected_account_record"
    ] == {
        "identity_path": "account_id",
        "revision_path": "revision",
        "create_expected_revision": 0,
        "update_expected_revision_path": "revision",
    }
    assert access_show.metadata["webui_data_binding"]["grant_record"] == {
        "identity_path": "grant_id",
        "revision_path": "revision",
        "create_expected_revision": 0,
        "update_expected_revision_path": "revision",
    }
    assert access_show.metadata["webui_data_binding"]["result_paths"]["roles"] == (
        "response.result.access.sections.roles"
    )
    assert {
        "sections": "response.result.access.sections",
        "permissions": "response.result.access.sections.permissions",
        "release_readiness": "response.result.access.sections.release_readiness",
        "activity": "response.result.access.sections.activity",
    }.items() <= access_show.metadata["webui_data_binding"]["result_paths"].items()
    assert access_show.metadata["webui_data_binding"]["examples"][
        "connected_account"
    ]["revision"] == 3
    users_surface = users.output_schema["properties"]["result"]["properties"][
        "users_access"
    ]
    subject = users_surface["properties"]["subjects"]["items"]
    assert subject["required"] == [
        "subject_ref",
        "kind",
        "display_label",
        "eligible_for_application_access",
    ]
    assert users.metadata["webui_data_binding"]["subject_choice"] == {
        "value_path": "subject_ref",
        "label_paths": ["display_label"],
        "eligibility_path": "eligible_for_application_access",
    }
    assert users_surface["properties"]["activity_page"]["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 200,
    }
    app_result = applications_plane.handlers()["applications.access.show"](
        {"application_id": "app_recipes", "activity_limit": 25}, dry_run=True
    )
    users_result = applications_plane.handlers()["applications.access.users"](
        {"activity_limit": 30}, dry_run=True
    )

    assert app_result["access"]["sections"]["access"] == []
    assert users_result["users_access"]["guests"] == []
    assert [call[0] for call in stub.calls] == [
        "get_application_access_surface",
        "get_users_access_surface",
    ]
    assert stub.calls[0][2]["activity_limit"] == 25
    assert stub.calls[1][2]["activity_limit"] == 30


def test_application_access_connected_account_dry_run_never_mutates(
    monkeypatch,
) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.access.connected_account"](
        {
            "application_id": "app_recipes",
            "release_digest": "sha256:" + "a" * 64,
            "account_id": "calendar-user",
            "provider_id": "calendar",
            "subject_ref": "user:owner",
            "mode": "delegated_user",
            "status": "connected",
            "expected_revision": 0,
            "idempotency_key": "calendar-connect-1",
        },
        dry_run=True,
    )

    assert result == {
        "would_update": True,
        "application_id": "app_recipes",
        "account_id": "calendar-user",
    }
    assert stub.calls == []


def test_applications_plane_plans_reviewed_component_relocation(monkeypatch) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)
    contract = {item.id: item for item in applications_plane.contracts()}[
        "applications.plan"
    ]

    assert {"relocate_component", "install_component", "remove_component"}.issubset(
        set(contract.input_schema["properties"]["kind"]["enum"])
    )
    result = applications_plane.handlers()["applications.plan"](
        {
            "application_id": "app_recipes",
            "kind": "relocate_component",
            "component_ref": "scenario:recipes",
            "target_node_id": "node-office",
            "expected_revision": 4,
            "idempotency_key": "relocate-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["operation"]["operation_id"] == "appop.relocate"
    assert stub.calls == [
        (
            "plan_relocate_component",
            ("app_recipes",),
            {
                "component_ref": "scenario:recipes",
                "target_node_id": "node-office",
                "expected_revision": 4,
                "actor_ref": "user:owner",
                "subnet_ref": "subnet:sn_home",
                "capability": "applications.plan",
                "idempotency_key": "relocate-1",
            },
        )
    ]


def test_application_access_form_lists_normalize_without_splitting_identifiers() -> (
    None
):
    assert applications_plane._string_list(
        "editor, viewer\neditor\nworkspace.read"
    ) == ("editor", "viewer", "workspace.read")
    assert applications_plane._string_list(["audit:allow", "audit:deny"]) == (
        "audit:allow",
        "audit:deny",
    )

    grant = next(
        item
        for item in applications_plane.contracts()
        if item.id == "applications.access.grant"
    )
    role_input = grant.input_schema["properties"]["application_roles"]
    assert {option["type"] for option in role_input["oneOf"]} == {
        "array",
        "string",
    }


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


def test_development_report_submission_uses_bounded_authenticated_context(
    monkeypatch,
) -> None:
    stub = _StubSdk()
    monkeypatch.setattr(applications_plane, "_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.submit_development_report"](
        {
            "application_id": "app_recipes",
            "summary": "Import fails",
            "details": "Expected import to complete.",
            "idempotency_key": "report-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["report"]["report_id"] == "report.1"
    assert stub.calls[0][2]["actor_ref"] == "user:owner"
    assert stub.calls[0][2]["subnet_ref"] == "subnet:sn_home"
    assert stub.calls[0][2]["capability"] == "applications.report"


def test_builder_development_mcp_forwards_narrow_authority(monkeypatch) -> None:
    stub = _StubBuilderSdk()
    monkeypatch.setattr(applications_plane, "_builder_sdk", lambda: stub)

    result = applications_plane.handlers()["applications.development.create"](
        {
            "application_id": "applications",
            "title": "Applications",
            "summary": "Application lifecycle manager",
            "template": "empty",
            "visibility": "private",
            "protection": {
                "system_application": True,
                "bootstrap_capable": True,
                "active_installation_removable": False,
                "recovery_surfaces": ["cli", "mcp"],
            },
            "expected_revision": 0,
            "idempotency_key": "create-applications-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["status"] == "succeeded"
    assert stub.calls[0][1] == ("applications",)
    assert stub.calls[0][2]["subnet_ref"] == "subnet:sn_home"
    assert stub.calls[0][2]["capability"] == "applications.develop"
    assert stub.calls[0][2]["protection"]["system_application"] is True

    updated = applications_plane.handlers()["applications.development.update_metadata"](
        {
            "application_id": "applications",
            "title": "Applications",
            "summary": "Manage installed applications and releases.",
            "categories": ["System", "Management"],
            "expected_revision": 1,
            "idempotency_key": "metadata-applications-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert updated["status"] == "succeeded"
    assert stub.calls[1][0] == "update_application_metadata"
    assert stub.calls[1][2]["categories"] == ("System", "Management")
    assert stub.calls[1][2]["capability"] == "applications.develop"

    deleted = applications_plane.handlers()["applications.development.delete"](
        {
            "application_id": "applications",
            "expected_manifest_digest": "sha256:" + "a" * 64,
            "expected_primary_ref": "scenario:applications",
            "confirmed": True,
            "expected_revision": 2,
            "idempotency_key": "delete-applications-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert deleted["status"] == "succeeded"
    assert stub.calls[2][0] == "delete_application_development"
    assert stub.calls[2][2]["confirmed"] is True
    assert stub.calls[2][2]["capability"] == "applications.develop"

    published = applications_plane.handlers()[
        "applications.development.publish_to_registry"
    ](
        {
            "application_id": "applications",
            "release_digest": "sha256:" + "a" * 64,
            "release_notes": "Public beta",
            "expected_revision": 2,
            "idempotency_key": "publish-applications-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert published["status"] == "succeeded"
    assert stub.calls[3][0] == "publish_to_registry"
    assert stub.calls[3][2]["capability"] == "applications.publish"

    recovered = applications_plane.handlers()[
        "applications.development.reconcile_operation"
    ](
        {"operation_id": "appdevop.unknown", "_mcp_context": _context()},
        dry_run=False,
    )

    assert recovered["operation"]["status"] == "succeeded"
    assert stub.calls[4][2]["capability"] == "applications.recover"
    assert stub.calls[4][2]["subnet_ref"] == "subnet:sn_home"


def test_builder_trial_records_authenticated_permission_decision(monkeypatch) -> None:
    stub = _StubBuilderSdk()
    monkeypatch.setattr(applications_plane, "_builder_sdk", lambda: stub)

    result = applications_plane.handlers()[
        "applications.development.create_trial"
    ](
        {
            "application_id": "gmail_cbs_cleanroom",
            "source_webspace_id": "desktop-dev",
            "permission_decision": True,
            "expected_revision": 2,
            "idempotency_key": "gmail-trial-019",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["status"] == "succeeded"
    assert stub.calls[0][0] == "create_trial"
    decision = stub.calls[0][2]["permission_decision"]
    assert decision == {
        "approved": True,
        "actor": "user:owner",
        "actor_type": "user",
        "approval_id": (
            "application-trial:gmail_cbs_cleanroom:gmail-trial-019"
        ),
        "reason": "explicit_trial_permission_approval",
    }
    assert stub.calls[0][2]["capability"] == "applications.develop"


def test_builder_trial_omits_permission_decision_when_not_supplied(monkeypatch) -> None:
    stub = _StubBuilderSdk()
    monkeypatch.setattr(applications_plane, "_builder_sdk", lambda: stub)

    applications_plane.handlers()["applications.development.create_trial"](
        {
            "application_id": "permissionless_app",
            "expected_revision": 1,
            "idempotency_key": "permissionless-trial-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert stub.calls[0][2]["permission_decision"] is None


def test_applications_contract_descriptor_and_capability_profile_are_published() -> (
    None
):
    descriptor = get_descriptor_set("application_contracts")
    schemas = descriptor["payload"]["schemas"]
    capabilities = {item["capability"] for item in list_capability_classes()}

    assert len(schemas) == 16
    assert schemas["application.v1.schema.json"]["title"] == "AdaOS Application v1"
    assert schemas["application.release-evidence-bundle.v1.schema.json"]["title"] == (
        "AdaOS Application Release Evidence Bundle v1"
    )
    groups = {
        item["group_id"]: item for item in descriptor["payload"]["operation_groups"]
    }
    assert {
        "catalog_and_detail",
        "lifecycle",
        "access",
        "setup_and_placement",
        "builder_lifecycle",
        "trial_and_prerelease",
    } == set(groups)
    assert "applications.list" in groups["catalog_and_detail"]["tool_ids"]
    assert "applications.apply" in groups["lifecycle"]["tool_ids"]
    assert "applications.access.grant" in groups["access"]["tool_ids"]
    assert {
        "applications.read",
        "applications.plan",
        "applications.apply",
        "applications.trial.install",
        "applications.publisher.read",
        "applications.report",
        "applications.publisher.triage",
        "applications.develop",
        "applications.publish",
        "applications.recover",
    } <= capabilities
    assert {
        "applications.read",
        "applications.plan",
        "applications.apply",
        "applications.trial.install",
        "applications.publisher.read",
        "applications.report",
        "applications.publisher.triage",
        "applications.recover",
    } <= set(DEFAULT_CAPABILITY_PROFILES["ApplicationsOperator"])


def test_plane_handler_signatures_match_root_mcp_dispatch() -> None:
    for tool_id, handler in applications_plane.handlers().items():
        signature = inspect.signature(handler)
        assert list(signature.parameters) == ["arguments", "dry_run"]
        expected_adapter = (
            "sdk.builder.applications"
            if tool_id.startswith("applications.development.")
            else "sdk.applications"
        )
        assert _execution_adapter_for_tool(tool_id) == expected_adapter
