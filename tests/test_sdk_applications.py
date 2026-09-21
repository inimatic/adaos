from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.sdk import applications
from adaos.sdk.core.exporter import export
from adaos.services.applications import (
    ApplicationDevelopmentCoordinator,
    register_development_report_service,
)


class _StubService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def plan_operation(self, *args, **kwargs):
        self.calls.append(("plan_operation", args, kwargs))
        return _Record({"operation_id": "appop.1", "plan_digest": "sha256:" + "a" * 64})

    def apply_operation(self, *args, **kwargs):
        self.calls.append(("apply_operation", args, kwargs))
        return _Record({"operation_id": args[0], "status": "succeeded"})


class _Record:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return dict(self.payload)


def test_installation_summary_projects_local_beta_without_stable_installation() -> None:
    release_digest = "sha256:" + "a" * 64
    summary = applications._installation_summary(
        {
            "installed": True,
            "installation": None,
            "subscription": None,
            "installed_release": None,
            "active_release": {
                "version": "1.3.0-beta.1",
                "release_digest": release_digest,
            },
            "local_beta_active": True,
            "auto_update_enabled": False,
            "runtime_selections": [
                {
                    "webspace_id": "desktop",
                    "source": "local_trial",
                    "release_digest": release_digest,
                    "runtime_root_ref": "trial:desktop-beta",
                    "updated_at": "2026-09-20T18:42:32+00:00",
                }
            ],
        },
        webspace_id="desktop",
    )

    assert summary == {
        "schema": "adaos.application.installation_summary.v1",
        "installed": True,
        "status": "beta_active",
        "source": "local_trial",
        "version": "1.3.0-beta.1",
        "release_digest": release_digest,
        "updated_at": "2026-09-20T18:42:32+00:00",
        "webspace_id": "desktop",
        "runtime_root_ref": "trial:desktop-beta",
        "update_track": None,
        "update_policy": None,
        "auto_update_enabled": False,
        "local_beta_active": True,
    }


def test_installation_summary_prefers_selected_webspace_runtime() -> None:
    summary = applications._installation_summary(
        {
            "installed": True,
            "installation": {"status": "active", "updated_at": "stable-time"},
            "subscription": {
                "update_track": "stable",
                "update_policy": "auto_compatible",
            },
            "installed_release": {"version": "1.2.3", "release_digest": "stable"},
            "auto_update_enabled": True,
            "runtime_selections": [
                {
                    "webspace_id": "other",
                    "source": "local_trial",
                    "release_digest": "other-beta",
                },
                {
                    "webspace_id": "desktop",
                    "source": "installed",
                    "release_digest": "desktop-stable",
                },
            ],
        },
        webspace_id="desktop",
    )

    assert summary["status"] == "active"
    assert summary["source"] == "installation"
    assert summary["version"] == "1.2.3"
    assert summary["update_track"] == "stable"
    assert summary["auto_update_enabled"] is True


def test_active_release_follows_the_selected_webspace_beta() -> None:
    beta_a = "sha256:" + "a" * 64
    beta_b = "sha256:" + "b" * 64
    model = {
        "installed_release": {"version": "1.0.0", "release_digest": "stable"},
        "active_release": None,
        "local_beta_releases": [
            {"version": "1.1.0-beta.1", "release_digest": beta_a},
            {"version": "1.2.0-beta.1", "release_digest": beta_b},
        ],
        "runtime_selections": [
            {
                "webspace_id": "desktop",
                "source": "local_trial",
                "release_digest": beta_a,
            },
            {
                "webspace_id": "office",
                "source": "local_trial",
                "release_digest": beta_b,
            },
        ],
    }

    selected = applications._active_release_for_webspace(
        model, webspace_id="office"
    )

    assert selected["version"] == "1.2.0-beta.1"
    assert selected["release_digest"] == beta_b


def test_application_service_uses_authority_state_in_trial(
    monkeypatch, tmp_path: Path
) -> None:
    authority = tmp_path / "authority"
    trial = tmp_path / "trial"
    observed: list[Path] = []
    service = object()
    monkeypatch.setattr(
        applications,
        "require_ctx",
        lambda _label: SimpleNamespace(
            authority_state_dir=authority,
            paths=SimpleNamespace(state_dir=lambda: trial),
        ),
    )
    monkeypatch.setattr(
        applications,
        "get_application_service",
        lambda state_dir: observed.append(state_dir) or service,
    )

    assert applications._service() is service
    assert observed == [authority.resolve()]


def test_identity_read_is_bounded_and_does_not_scan_or_create(monkeypatch):
    record = SimpleNamespace(
        application_id="example",
        publisher_ref="subnet:other",
        publisher={"display_name": "Registered publisher", "private": "not exported"},
    )
    calls = []

    def get(application_id):
        calls.append(application_id)
        if application_id == "missing":
            raise FileNotFoundError(application_id)
        return record

    monkeypatch.setattr(
        applications,
        "_service",
        lambda: SimpleNamespace(store=SimpleNamespace(get_application=get)),
    )
    monkeypatch.setattr(
        applications,
        "list_applications",
        lambda: pytest.fail("No catalog/runtime scan"),
    )
    assert applications.get_identity("example") == {
        "application_id": "example",
        "publisher_ref": "subnet:other",
        "display_name": "Registered publisher",
        "source": "application_registry",
    }
    with pytest.raises(FileNotFoundError):
        applications.get_identity("missing")
    assert calls == ["example", "missing"]


def test_development_projects_use_authority_projection_and_hide_paths(
    monkeypatch, tmp_path: Path
) -> None:
    authority = tmp_path / "authority"
    observed: dict[str, object] = {}

    class Projection:
        def __init__(self, state_dir: Path) -> None:
            observed["state_dir"] = state_dir

        def list_development_projects(self, **kwargs):
            observed["kwargs"] = kwargs
            return [
                {
                    "id": "builder",
                    "title": "Builder",
                    "description": "Development workbench",
                    "version": "0.2.171",
                    "updated_at": "2026-09-19T05:39:56Z",
                    "stage": "alpha",
                    "visibility": "unlisted",
                    "primary_ref": "scenario:builder",
                    "source_kind": "dev_project",
                    "validation_status": "valid",
                    "source_path": "D:/private/dev/projects/builder",
                    "components": {"owned": [{"ref": "scenario:builder"}]},
                }
            ]

    monkeypatch.setattr(
        applications,
        "require_ctx",
        lambda _label: SimpleNamespace(
            authority_state_dir=authority,
            paths=SimpleNamespace(state_dir=lambda: tmp_path / "trial"),
        ),
    )
    monkeypatch.setattr(
        applications, "_admit_active_skill_capability", lambda _capability: None
    )
    monkeypatch.setattr(applications, "ApplicationRegistryProjection", Projection)

    result = applications.list_development_projects(
        profile="desktop", query="build", limit=7000
    )

    assert observed == {
        "state_dir": authority,
        "kwargs": {"profile": "desktop", "query": "build", "limit": 5000},
    }
    assert result == [
        {
            "id": "builder",
            "title": "Builder",
            "description": "Development workbench",
            "version": "0.2.171",
            "updated_at": "2026-09-19T05:39:56Z",
            "stage": "alpha",
            "visibility": "unlisted",
            "primary_ref": "scenario:builder",
            "source_kind": "dev_project",
            "validation_status": "valid",
            "status": "development",
        }
    ]


def test_sdk_application_mutations_forward_complete_review_context(monkeypatch) -> None:
    stub = _StubService()
    monkeypatch.setattr(applications, "_service", lambda: stub)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:sn_home")
    monkeypatch.setattr(
        applications, "_admit_active_skill_capability", lambda _capability: None
    )

    plan = applications.plan_install(
        "app_recipes",
        release_digest="sha256:" + "b" * 64,
        expected_revision=3,
        actor_ref="skill:applications",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-4",
    )
    result = applications.apply_operation(
        "appop.1",
        plan_digest=plan["plan_digest"],
        actor_ref="skill:applications",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
        idempotency_key="install-4",
    )

    assert result["status"] == "succeeded"
    assert stub.calls[0] == (
        "plan_operation",
        ("app_recipes", "install"),
        {
            "release_digest": "sha256:" + "b" * 64,
            "expected_revision": 3,
            "actor_ref": "skill:applications",
            "subnet_ref": "subnet:sn_home",
            "capability": "applications.plan",
            "idempotency_key": "install-4",
            "data_policy": "retain",
            "access_redemption_id": None,
        },
    )
    assert stub.calls[1] == (
        "apply_operation",
        ("appop.1",),
        {
            "plan_digest": "sha256:" + "a" * 64,
            "idempotency_key": "install-4",
            "actor_ref": "skill:applications",
            "subnet_ref": "subnet:sn_home",
            "capability": "applications.apply",
        },
    )


def test_successful_install_projects_application_to_home(monkeypatch) -> None:
    class Service:
        def apply_operation(self, *_args, **_kwargs):
            return _Record(
                {
                    "operation_id": "appop.install",
                    "application_id": "reading_list",
                    "kind": "install",
                    "status": "succeeded",
                }
            )

    projected = []
    monkeypatch.setattr(applications, "_service", Service)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:sn_home")
    monkeypatch.setattr(
        applications, "_admit_active_skill_capability", lambda _capability: None
    )
    monkeypatch.setattr(
        applications,
        "_sync_home_installation",
        lambda application_id, **kwargs: projected.append(
            (application_id, kwargs)
        )
        or {"pinned": True, "webspace_id": kwargs["webspace_id"]},
    )

    result = applications.apply_operation(
        "appop.install",
        plan_digest="sha256:" + "a" * 64,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
        idempotency_key="install-reading-list",
        webspace_id="family",
    )

    assert result["home"] == {"pinned": True, "webspace_id": "family"}
    assert projected == [
        ("reading_list", {"installed": True, "webspace_id": "family"})
    ]


def test_home_pin_changes_only_presentation_overlay(monkeypatch) -> None:
    snapshot = SimpleNamespace(
        installed=SimpleNamespace(
            apps=["scenario:reading_list"],
            widgets=[],
            removed_apps=[],
            removed_widgets=[],
        ),
        pinned_applications=["scenario:reading_list", "scenario:notes"],
    )
    writes = []

    class Desktop:
        def get_snapshot(self, webspace_id):
            assert webspace_id == "family"
            return snapshot

        def set_pinned_applications_with_live_room(self, values, webspace_id):
            writes.append((values, webspace_id))

    monkeypatch.setattr(
        applications,
        "get_application",
        lambda application_id, **_kwargs: {
            "application": {
                "application_id": application_id,
                "entrypoints": [
                    {
                        "entrypoint_id": "main",
                        "presentation_ref": "scenario:reading_list",
                    }
                ],
            },
            "installed": True,
        },
    )
    monkeypatch.setattr(applications, "WebDesktopService", Desktop)

    result = applications.set_home_pinned(
        "reading_list", pinned=False, webspace_id="family"
    )

    assert result["pinned"] is False
    assert result["projection_reconciled"] is False
    assert writes == [(["scenario:notes"], "family")]


def test_home_pin_materializes_missing_desktop_projection_for_subnet_install(
    monkeypatch,
) -> None:
    snapshot = SimpleNamespace(
        installed=SimpleNamespace(
            apps=["scenario:notes"],
            widgets=["notes-summary"],
            removed_apps=["scenario:reading_list"],
            removed_widgets=["retired-widget"],
        ),
        pinned_applications=["scenario:notes"],
    )
    toggle_writes = []
    pinned_writes = []

    class Desktop:
        def get_snapshot(self, webspace_id):
            assert webspace_id == "family"
            return snapshot

        def toggle_install_with_live_room(self, item_type, target_id, webspace_id):
            toggle_writes.append((item_type, target_id, webspace_id))

        def set_pinned_applications_with_live_room(self, values, webspace_id):
            pinned_writes.append((values, webspace_id))

    monkeypatch.setattr(
        applications,
        "get_application",
        lambda application_id, **_kwargs: {
            "application": {
                "application_id": application_id,
                "entrypoints": [
                    {
                        "entrypoint_id": "main",
                        "presentation_ref": "scenario:reading_list",
                    }
                ],
            },
            "installed": True,
        },
    )
    monkeypatch.setattr(applications, "WebDesktopService", Desktop)

    result = applications.set_home_pinned(
        "reading_list", pinned=True, webspace_id="family"
    )

    assert result["pinned"] is True
    assert result["projection_reconciled"] is True
    assert toggle_writes == [("app", "scenario:reading_list", "family")]
    assert pinned_writes == []


def test_application_list_reads_home_and_placement_inventory_once(monkeypatch) -> None:
    models = [
        {
            "application": {
                "application_id": application_id,
                "visibility": "private",
                "entrypoints": [
                    {
                        "entrypoint_id": "main",
                        "presentation_ref": f"scenario:{application_id}",
                    }
                ],
            },
            "installed": True,
            "channels": {},
        }
        for application_id in ("notes", "reading_list")
    ]
    snapshot = SimpleNamespace(
        installed=SimpleNamespace(apps=["scenario:notes", "scenario:reading_list"]),
        pinned_applications=["scenario:notes"],
    )
    home_reads: list[str] = []
    placement_reads: list[tuple[str, ...]] = []

    class Desktop:
        def get_snapshot(self, webspace_id):
            home_reads.append(webspace_id)
            return snapshot

    monkeypatch.setattr(applications, "_application_models", lambda **_kwargs: models)
    monkeypatch.setattr(applications, "_local_development_index", dict)
    monkeypatch.setattr(applications, "WebDesktopService", Desktop)
    monkeypatch.setattr(
        applications,
        "_execution_placement_index",
        lambda application_ids: placement_reads.append(tuple(application_ids)) or {},
    )

    listed = applications.list_applications(webspace_id="desktop")

    assert home_reads == ["desktop"]
    assert placement_reads == [("notes", "reading_list")]
    assert [item["home"]["pinned"] for item in listed] == [True, False]


def test_application_show_enriches_only_the_requested_model(monkeypatch) -> None:
    models = [
        {
            "application": {"application_id": application_id},
            "installed": True,
            "channels": {},
        }
        for application_id in ("notes", "reading_list", "builder")
    ]
    enriched: list[list[str]] = []

    def enrich(selected, **_kwargs):
        enriched.append(
            [item["application"]["application_id"] for item in selected]
        )
        return selected

    monkeypatch.setattr(applications, "_application_models", lambda **_kwargs: models)
    monkeypatch.setattr(applications, "_local_development_index", dict)
    monkeypatch.setattr(applications, "_enrich_application_models", enrich)

    shown = applications.get_application("reading_list", webspace_id="desktop")

    assert shown["application"]["application_id"] == "reading_list"
    assert enriched == [["reading_list"]]


def test_sdk_application_access_helpers_forward_review_context(monkeypatch) -> None:
    calls = []

    class Access:
        def grant_access(self, *args, **kwargs):
            calls.append(("grant_access", args, kwargs))
            return _Record({"grant_id": "appgrant.1"})

        def revoke_access(self, *args, **kwargs):
            calls.append(("revoke_access", args, kwargs))
            return _Record({"grant_id": args[0], "status": "revoked"})

        def decide(self, *args, **kwargs):
            calls.append(("decide", args, kwargs))
            return _Record({"decision": "allow"})

    access = Access()
    monkeypatch.setattr(
        applications, "_service", lambda: SimpleNamespace(store=SimpleNamespace())
    )
    monkeypatch.setattr(
        applications, "ApplicationAccessService", lambda _service: access
    )
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:sn_home")
    monkeypatch.setattr(
        applications, "_admit_active_skill_capability", lambda _capability: None
    )

    assert applications.grant_application_access(
        "app_demo",
        release_digest="sha256:" + "a" * 64,
        subject_ref="user:masha",
        application_roles=("editor",),
        issuer_ref="",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
        idempotency_key="grant-masha-editor",
        permission_ceiling=("workspace.read",),
    ) == {"grant_id": "appgrant.1"}
    assert applications.decide_application_access(
        "app_demo",
        release_digest="sha256:" + "a" * 64,
        subject_ref="user:masha",
        permission_id="workspace.read",
        app_capability="app.view",
        component_capabilities=("workspace.read",),
        actor_chain={"component_ref": "scenario:demo"},
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="decide-masha-read",
    ) == {"decision": "allow"}
    assert applications.revoke_application_access(
        "appgrant.1",
        issuer_ref="",
        expected_revision=1,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    ) == {"grant_id": "appgrant.1", "status": "revoked"}

    assert calls[0] == (
        "grant_access",
        ("app_demo",),
        {
            "release_digest": "sha256:" + "a" * 64,
            "subject_ref": "user:masha",
            "application_roles": ("editor",),
            "permission_ceiling": ("workspace.read",),
            "explicit_denies": (),
            "constraints": None,
            "expires_at": None,
            "issuer_ref": "user:owner",
            "idempotency_key": "grant-masha-editor",
        },
    )
    assert calls[1][0] == "decide"
    assert calls[1][2]["actor_chain"] == {
        "actor_ref": "user:owner",
        "subnet_ref": "subnet:sn_home",
        "capability": "applications.plan",
        "component_ref": "scenario:demo",
    }
    assert calls[2] == (
        "revoke_access",
        ("appgrant.1",),
        {"issuer_ref": "user:owner", "expected_revision": 1},
    )


def test_sdk_application_surface_has_no_raw_path_or_process_parameters() -> None:
    forbidden = {
        "path",
        "filesystem_path",
        "command",
        "process",
        "git_credentials",
        "registry_path",
    }
    for name in applications.__all__:
        function = getattr(applications, name)
        assert forbidden.isdisjoint(inspect.signature(function).parameters), name


def test_application_sdk_is_discoverable_for_builder_context() -> None:
    metadata = export(level="std", query="application install prerelease", limit=40)
    names = {item["name"] for item in metadata["tools"]}

    assert "adaos.sdk.applications.plan_install" in names
    assert "adaos.sdk.applications.plan_update_track" in names
    assert "adaos.sdk.applications.resolve_trial_link" in names


def test_application_reads_project_only_existing_local_developments(
    monkeypatch, tmp_path: Path
) -> None:
    context = SimpleNamespace(
        paths=SimpleNamespace(
            state_dir=lambda: tmp_path,
            dev_dir=lambda: tmp_path / "dev",
        ),
        config=SimpleNamespace(subnet_id_value="sn_home"),
    )
    monkeypatch.setattr(applications, "require_ctx", lambda _reason: context)

    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    coordinator.execute(
        "create",
        "applications",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.develop",
        expected_revision=0,
        idempotency_key="create-applications",
        intent={"template": "empty", "source_webspace_id": "desktop"},
        callback=lambda: {"ok": True},
    )
    coordinator.execute(
        "create",
        "foreign",
        actor_ref="user:guest",
        subnet_ref="subnet:foreign",
        capability="applications.develop",
        expected_revision=0,
        idempotency_key="create-foreign",
        intent={"template": "empty"},
        callback=lambda: {"ok": True},
    )

    class Service:
        def list_models(self, **_kwargs):
            return [
                {
                    "application": {
                        "application_id": "applications",
                        "visibility": "private",
                        "entrypoints": [
                            {
                                "entrypoint_id": "main",
                                "presentation_ref": "scenario:applications",
                            }
                        ],
                    },
                    "installed": True,
                    "channels": {},
                },
                {
                    "application": {
                        "application_id": "foreign",
                        "visibility": "public",
                        "entrypoints": [
                            {
                                "entrypoint_id": "main",
                                "presentation_ref": "scenario:foreign",
                            }
                        ],
                    },
                    "installed": False,
                    "channels": {"stable": "sha256:" + "a" * 64},
                },
                {
                    "application": {
                        "application_id": "local-beta",
                        "visibility": "private",
                        "entrypoints": [
                            {
                                "entrypoint_id": "main",
                                "presentation_ref": "scenario:local-beta",
                            }
                        ],
                    },
                    "installed": False,
                    "local_beta_active": True,
                    "channels": {},
                },
            ]

    monkeypatch.setattr(applications, "_service", lambda: Service())
    monkeypatch.setattr(
        applications,
        "_development_workflow_summary",
        lambda object_type, object_id: {
            "phase": "prototype",
            "status": "working",
            "revision": "010",
            "stable": False,
            "accepted": False,
            "publication_status": "not_started",
            "updated_at": None,
        }
        if (object_type, object_id) == ("scenario", "applications")
        else None,
    )

    developed = applications.list_applications(developed_only=True)
    catalog = applications.list_applications(catalog_only=True)
    available = applications.list_applications(available_only=True)

    assert [item["application"]["application_id"] for item in developed] == [
        "applications"
    ]
    assert developed[0]["local_development"] == {
        "exists": True,
        "status": "working",
        "latest_action": "create",
        "updated_at": developed[0]["local_development"]["updated_at"],
        "operation_count": 1,
        "transport_status": "succeeded",
        "phase": "prototype",
        "revision": "010",
        "stable": False,
        "accepted": False,
        "builder": {
            "selected_object_type": "scenario",
            "selected_object_id": "applications",
            "source_webspace_id": "desktop",
            "preview_webspace_id": "desktop-dev",
        },
        "publication_status": "not_started",
    }
    assert [item["application"]["application_id"] for item in catalog] == ["foreign"]
    assert catalog[0]["local_development"] is None
    assert [item["application"]["application_id"] for item in available] == [
        "applications",
        "foreign",
        "local-beta",
    ]


def test_application_read_survives_missing_development_project(
    monkeypatch, tmp_path: Path
) -> None:
    from adaos.sdk.developer import compositions

    context = SimpleNamespace(
        paths=SimpleNamespace(
            state_dir=lambda: tmp_path,
            dev_dir=lambda: tmp_path / "dev",
        ),
        config=SimpleNamespace(subnet_id_value="sn_home"),
    )
    monkeypatch.setattr(applications, "require_ctx", lambda _reason: context)
    monkeypatch.setattr(compositions, "require_ctx", lambda _reason: context)

    ApplicationDevelopmentCoordinator(tmp_path).execute(
        "create",
        "stale-app",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.develop",
        expected_revision=0,
        idempotency_key="create-stale-app",
        intent={"source_webspace_id": "desktop"},
        callback=lambda: {"ok": True},
    )

    class Service:
        def list_models(self, **_kwargs):
            return [
                {
                    "application": {
                        "application_id": "stale-app",
                        "legacy_project_id": "missing-project",
                        "visibility": "private",
                        "entrypoints": [
                            {
                                "entrypoint_id": "main",
                                "presentation_ref": "scenario:stale-app",
                            }
                        ],
                    },
                    "installed": False,
                    "channels": {},
                }
            ]

    monkeypatch.setattr(applications, "_service", lambda: Service())
    monkeypatch.setattr(applications, "_development_workflow_summary", lambda *_: None)

    models = applications.list_applications()

    assert len(models) == 1
    assert models[0]["local_development"]["deletion"] == {
        "project_id": "missing-project",
        "allowed": False,
        "reason": "development_project_unavailable",
    }
    assert models[0]["local_development"]["exists"] is False
    assert models[0]["local_development"]["status"] == "source_unavailable"
    assert applications.list_applications(developed_only=True) == []


def test_application_list_can_skip_development_enrichment(monkeypatch) -> None:
    class Service:
        def list_models(self, **_kwargs):
            return [
                {
                    "application": {
                        "application_id": "notes",
                        "visibility": "private",
                        "entrypoints": [],
                    },
                    "installed": True,
                    "channels": {},
                }
            ]

    monkeypatch.setattr(applications, "_service", lambda: Service())
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:home")
    monkeypatch.setattr(
        applications, "_workspace_project_read_models", lambda models: []
    )
    monkeypatch.setattr(
        applications,
        "_local_development_index",
        lambda: (_ for _ in ()).throw(
            AssertionError("development index must be skipped")
        ),
    )

    models = applications.list_applications(include_development=False)

    assert models[0]["application"]["application_id"] == "notes"
    assert models[0]["local_development"] is None


def test_application_list_includes_read_only_workspace_project_projection(
    monkeypatch, tmp_path: Path
) -> None:
    context = SimpleNamespace(
        paths=SimpleNamespace(state_dir=lambda: tmp_path),
        config=SimpleNamespace(subnet_id_value="sn_home"),
    )
    monkeypatch.setattr(applications, "require_ctx", lambda _reason: context)
    monkeypatch.setattr(applications, "_local_development_index", dict)

    class Service:
        def list_models(self, **_kwargs):
            return []

    class Projection:
        def __init__(self, _state_dir):
            pass

        def list_workspace_projects(self, **_kwargs):
            return [
                {
                    "id": "legacy_notes",
                    "version": "1.2.3",
                    "title": "Legacy Notes",
                    "description": "Indexed from the Workspace project manifest.",
                    "visibility": "listed",
                    "categories": ["productivity"],
                    "entrypoints": [
                        {
                            "id": "main",
                            "presentation": "scenario:legacy_notes",
                        }
                    ],
                    "permission_profile": {"required": [{"id": "notes.read"}]},
                    "manifest_digest": "sha256:" + "a" * 64,
                }
            ]

    monkeypatch.setattr(applications, "_service", lambda: Service())
    monkeypatch.setattr(applications, "ApplicationRegistryProjection", Projection)

    listed = applications.list_applications(available_only=True)

    assert len(listed) == 1
    assert listed[0]["application"]["application_id"] == "legacy_notes"
    assert listed[0]["application"]["aggregate_backed"] is False
    assert listed[0]["installed"] is True
    assert listed[0]["installed_release"]["version"] == "1.2.3"


def test_release_list_projects_read_only_workspace_project(monkeypatch) -> None:
    digest = "sha256:" + "a" * 64

    class Service:
        def list_releases(self, application_id):
            assert application_id == "legacy_notes"
            raise FileNotFoundError("Application not found: legacy_notes")

    monkeypatch.setattr(applications, "_service", lambda: Service())
    monkeypatch.setattr(
        applications,
        "_workspace_project_read_models",
        lambda _existing, application_id=None: [
            {
                "installed_release": {
                    "schema": "adaos.application.workspace_project_release.v1",
                    "application_id": application_id,
                    "version": "1.2.3",
                    "project_release": {
                        "project_id": application_id,
                        "version": "1.2.3",
                    },
                },
                "workspace_project": {"manifest_digest": digest},
            }
        ],
    )

    releases = applications.list_releases("legacy_notes")

    assert releases == [
        {
            "schema": "adaos.application.workspace_project_release.v1",
            "application_id": "legacy_notes",
            "version": "1.2.3",
            "release_digest": digest,
            "lifecycle": "stable",
            "channels": ["stable"],
            "project_release": {
                "schema": "adaos.artifact.workspace_project_release.v1",
                "project_id": "legacy_notes",
                "version": "1.2.3",
                "release_digest": digest,
                "components": [],
                "resolved_dependencies": [],
                "composition_lock": {"entrypoint_ids": []},
                "migration": {"required": False, "count": 0},
                "validation_evidence_count": 0,
                "private_source": "redacted",
            },
            "acceptance_evidence_count": 0,
        }
    ]


def test_workspace_project_access_surface_is_read_only(monkeypatch) -> None:
    projected = {
        "application": {
            "application_id": "legacy_notes",
            "aggregate_backed": False,
        },
        "installation": {"application_id": "legacy_notes", "revision": 0},
        "installed_release": {"application_id": "legacy_notes", "version": "1.2.3"},
        "workspace_project": {
            "manifest_digest": "sha256:" + "a" * 64,
            "permission_profile": {"required": [{"id": "notes.read"}]},
            "application_roles": [{"id": "viewer", "title": "Viewer"}],
        },
    }

    class Access:
        def application_detail(self, *_args, **_kwargs):
            raise FileNotFoundError("not migrated")

    monkeypatch.setattr(applications, "_access_management", lambda: Access())
    monkeypatch.setattr(
        applications, "get_application", lambda _application_id: projected
    )

    surface = applications.get_application_access_surface("legacy_notes")

    assert surface["managed"] is False
    assert surface["sections"]["permissions"]["profile"]["required"] == [
        {"id": "notes.read"}
    ]
    assert surface["sections"]["roles"] == [{"id": "viewer", "title": "Viewer"}]


def test_sdk_exposes_development_report_status_without_internal_store_access() -> None:
    class Reports:
        def list_reports(self):
            return [{"report_id": "report.1"}]

        def get_report(self, report_id):
            return {"report_id": report_id}

        def public_status(self, report_id):
            return {"report_id": report_id, "status": "accepted"}

        def list_publisher_intakes(self):
            return [{"report_id": "report.1", "status": "quarantined"}]

        def list_local_appeals(self, report_id=None):
            return [{"appeal_id": "appeal.1", "report_id": report_id}]

    register_development_report_service(Reports())
    try:
        assert applications.list_development_reports()[0]["report_id"] == "report.1"
        assert (
            applications.get_development_report_status("report.1")["status"]
            == "accepted"
        )
        assert (
            applications.list_development_report_intakes()[0]["status"] == "quarantined"
        )
        assert (
            applications.list_development_report_appeals("report.1")[0]["appeal_id"]
            == "appeal.1"
        )
    finally:
        register_development_report_service(None)


def test_sdk_report_mutations_require_local_narrow_capability(monkeypatch) -> None:
    class Reports:
        def __init__(self):
            self.calls = []

        def create_report(self, **kwargs):
            self.calls.append(("create_report", kwargs))
            return {"report": {"report_id": "report.1"}}

        def triage(self, report_id, **kwargs):
            self.calls.append(("triage", report_id, kwargs))
            return {"event": {"status": kwargs["outcome"]}}

    reports = Reports()
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:sn_home")
    monkeypatch.setattr(
        applications, "_admit_active_skill_capability", lambda _capability: None
    )
    register_development_report_service(reports)
    try:
        submitted = applications.submit_development_report(
            "app_recipes",
            summary="Failure",
            details="Expected A, observed B",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.report",
            idempotency_key="report-1",
        )
        assert submitted["report"]["report_id"] == "report.1"
        triaged = applications.triage_development_report(
            "report.1",
            outcome="declined",
            reason_code="not_reproduced",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.publisher.triage",
            idempotency_key="triage-1",
        )
        assert triaged["event"]["status"] == "declined"
        with pytest.raises(ValueError, match="applications.report"):
            applications.submit_development_report(
                "app_recipes",
                summary="Failure",
                details="Details",
                actor_ref="user:owner",
                subnet_ref="subnet:sn_home",
                capability="applications.apply",
                idempotency_key="report-2",
            )
        with pytest.raises(ValueError, match="local identity"):
            applications.submit_development_report(
                "app_recipes",
                summary="Failure",
                details="Details",
                actor_ref="user:owner",
                subnet_ref="subnet:foreign",
                capability="applications.report",
                idempotency_key="report-3",
            )
    finally:
        register_development_report_service(None)


def test_application_sdk_admits_active_skill_capability(monkeypatch) -> None:
    decisions = []
    context = SimpleNamespace(
        skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name="applications")),
    )
    monkeypatch.setattr(applications, "require_ctx", lambda _reason: context)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:home")
    monkeypatch.setattr(
        applications,
        "require_skill_capability",
        lambda ctx, capability: decisions.append((ctx, capability)),
    )

    identity = applications._mutation_identity(
        "user:owner",
        "subnet:home",
        "applications.apply",
        "apply-1",
        required_capability="applications.apply",
    )

    assert identity == (
        "user:owner",
        "subnet:home",
        "applications.apply",
        "apply-1",
    )
    assert decisions == [(context, "applications.apply")]
    with pytest.raises(ValueError, match="local identity"):
        applications._mutation_identity(
            "user:owner",
            "subnet:foreign",
            "applications.apply",
            "apply-2",
            required_capability="applications.apply",
        )


def test_sdk_release_reads_preserve_identity_and_redact_private_source(
    monkeypatch,
) -> None:
    digest = "sha256:" + "a" * 64
    package_digest = "sha256:" + "b" * 64
    raw_release = {
        "schema": "adaos.application.release.v1",
        "application_id": "app_private",
        "publisher_ref": "subnet:publisher",
        "legacy_project_id": "private",
        "version": "1.0.0",
        "release_digest": digest,
        "accepted_candidate_id": "candidate.private.1",
        "acceptance_evidence": [{"token": "secret", "path": "D:/private/source"}],
        "provenance_refs": [digest],
        "addresses_report_ids": [],
        "lifecycle": "stable",
        "project_release": {
            "schema": "adaos.artifact.project_release.v1",
            "project_id": "private",
            "version": "1.0.0",
            "source_ref": {
                "repository": "private/repository",
                "path_scope": ["secret/"],
            },
            "components": [
                {
                    "kind": "scenario",
                    "artifact_id": "private",
                    "version": "1.0.0",
                    "digest": package_digest,
                    "manifest_digest": digest,
                    "source_ref": {"repository": "private/repository"},
                    "materialization_path": "scenarios/private",
                }
            ],
            "resolved_dependencies": [],
            "permissions": ["network.read"],
            "migrations": [{"command": "private-migration"}],
            "validation_evidence": [{"log_path": "D:/private/log"}],
            "schema_locks": [],
            "migration_locks": [],
            "validation_evidence_refs": [digest],
            "release_digest": digest,
        },
    }

    class Releases:
        def list_releases(self, application_id):
            assert application_id == "app_private"
            return [raw_release]

    monkeypatch.setattr(applications, "_service", lambda: Releases())
    release = applications.list_releases("app_private")[0]
    serialized = str(release)

    assert release["release_digest"] == digest
    assert release["publisher_ref"] == "subnet:publisher"
    assert release["project_release"]["components"][0]["digest"] == package_digest
    assert release["project_release"]["private_source"] == "redacted"
    assert release["project_release"]["migration"] == {"required": True, "count": 1}
    for private_value in (
        "private/repository",
        "D:/private/source",
        "D:/private/log",
        "private-migration",
        "materialization_path",
        "secret",
    ):
        assert private_value not in serialized
