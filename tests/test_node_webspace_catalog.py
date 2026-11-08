from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import node_api
from adaos.services.scenario.webspace_runtime import WebspaceInfo
from adaos.services.scenario import webspace_runtime
from adaos.services.workspaces import index as workspace_index
from adaos.services.applications import runtime_selection
from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable


def test_desktop_catalog_does_not_scan_application_sources_on_api_event_loop(monkeypatch):
    owner = threading.get_ident()
    calls = []

    class Catalog:
        def list(self, *, mode, validate):
            calls.append(threading.get_ident())
            # Cold runtime selection may hold a single-flight lock in another
            # worker. Waiting for it on the owner loop stalls unrelated HTTP/WS.
            assert threading.get_ident() != owner, "catalog scan blocks API owner loop"
            assert mode == "mixed"
            assert validate is False, "inventory must not verify every desktop's runtime packages"
            return [WebspaceInfo(id="desktop-dev", title="Development", created_at=1,
                kind="dev", home_scenario="web_desktop", source_mode="dev", is_dev=True)]

    monkeypatch.setattr(node_api, "WebspaceService", Catalog)
    monkeypatch.setattr(node_api, "load_config", lambda: SimpleNamespace(role="hub"))
    monkeypatch.setattr(node_api.workspace_index, "workspace_catalog_version", lambda: 7)
    monkeypatch.setattr(node_api, "_local_node_id", lambda: "test-node")
    monkeypatch.setattr(node_api, "_local_node_label", lambda: "Test node")
    result = asyncio.run(node_api.node_yjs_webspaces())
    assert calls and result["ok"] and result["accepted"]
    assert result["catalog_version"] == 7
    assert result["items"][0]["id"] == "desktop-dev"


def test_inventory_reads_manifests_without_opening_documents_or_verifying_sources(monkeypatch):
    workspace_index.ensure_workspace("legacy-desktop")
    workspace_index.ensure_workspace("trial-desktop")
    workspace_index.set_workspace_current_scenario_overlay("trial-desktop", "trial-app")

    def unexpected(*args, **kwargs):
        raise AssertionError("catalog enumeration must not load documents or verify runtime sources")

    monkeypatch.setattr(webspace_runtime, "_try_read_live_current_scenario", unexpected)
    monkeypatch.setattr(webspace_runtime, "_scenario_exists_for_source_mode", unexpected)
    entries = {item.id: item for item in webspace_runtime.WebspaceService().list(validate=False)}
    assert {"legacy-desktop", "trial-desktop"} <= entries.keys()
    assert entries["legacy-desktop"].current_scenario is None
    assert entries["trial-desktop"].current_scenario == "trial-app"
    assert entries["trial-desktop"].home_scenario == "web_desktop"
    assert entries["trial-desktop"].home_scenario_exists is None
    assert entries["trial-desktop"].current_scenario_exists is None


def test_lightweight_http_catalog_still_filters_access_and_revocation(monkeypatch, tmp_path):
    from adaos.apps.api.desktop_access import require_desktop_caller
    from adaos.domain.personalization_access import SessionKey, SubjectRef
    from adaos.services import personalization_runtime
    from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
    from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore

    owner = SubjectRef("user", "catalog-owner")
    visitor = SubjectRef("user", "catalog-visitor")
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"), owner=owner)
    policy = DesktopAccessService(access)
    monkeypatch.setattr(personalization_runtime, "personalization_access_service", lambda: access)
    monkeypatch.setattr(personalization_runtime, "current_user_id", lambda: owner.id)
    monkeypatch.setattr(node_api, "load_config", lambda: SimpleNamespace(role="hub"))
    monkeypatch.setenv("ADAOS_TOKEN", "catalog-owner-secret")
    workspace_index.ensure_workspace("catalog-private")
    workspace_index.ensure_workspace("catalog-shared")
    grant = policy.share(DesktopPrincipal(owner), "catalog-shared", visitor)
    access.put_session(SessionKey(session_id="catalog-session", key_id="test", subject=visitor,
                                  expires_at=time.time() + 3600))
    token = policy.issue_credential(DesktopPrincipal(SubjectRef("session", "catalog-session"), "catalog-session"))["access_token"]

    def unexpected(*args, **kwargs):
        raise AssertionError("HTTP catalog must not validate runtime sources")

    monkeypatch.setattr(webspace_runtime, "_scenario_exists_for_source_mode", unexpected)
    app = FastAPI()
    app.add_api_route("/api/node/yjs/webspaces", node_api.node_yjs_webspaces,
                      dependencies=[Depends(require_desktop_caller)])
    with TestClient(app) as client:
        owner_response = client.get("/api/node/yjs/webspaces", headers={"X-AdaOS-Token": "catalog-owner-secret"})
        assert owner_response.status_code == 200
        assert owner_response.json()["can_create"] is True
        headers = {"X-AdaOS-Desktop-Credential": token}
        response = client.get("/api/node/yjs/webspaces", headers=headers)
        assert response.status_code == 200
        catalog = response.json()
        assert catalog["can_create"] is False
        assert [item["id"] for item in catalog["items"]] == ["catalog-shared"]
        assert catalog["items"][0]["can_administer"] is False
        # Changes to a hidden desktop must not change the visitor's version.
        workspace_index.ensure_workspace("another-hidden-desktop")
        assert client.get("/api/node/yjs/webspaces", headers=headers).json()["catalog_version"] == catalog["catalog_version"]
        policy.revoke(DesktopPrincipal(owner), "catalog-shared", grant["grant_id"])
        assert client.get("/api/node/yjs/webspaces", headers=headers).json()["items"] == []


def test_expired_trial_marks_its_desktop_degraded_without_hiding_healthy_desktops(monkeypatch):
    workspace_index.ensure_workspace("healthy-desktop")
    workspace_index.ensure_workspace("expired-preview")
    workspace_index.set_workspace_current_scenario_overlay("expired-preview", "expired-app")
    monkeypatch.setattr(runtime_selection, "selected_application", lambda *args: None)

    def selected(*args):
        if args[-1] == "expired-app":
            raise TrialRuntimeUnavailable("Trial has expired")
        return None

    monkeypatch.setattr(runtime_selection, "selected_trial", selected)
    # A same-named development source must not bypass the expired Trial pin.
    monkeypatch.setattr(webspace_runtime, "_scenario_exists_for_switch", lambda *args, **kwargs: True)
    entries = {item.id: item for item in webspace_runtime.WebspaceService().list()}
    assert not entries["healthy-desktop"].degraded
    expired = entries["expired-preview"]
    assert expired.degraded and expired.current_scenario_exists is False
    assert expired.validation_reason == "current_scenario_missing"
