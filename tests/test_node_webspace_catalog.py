from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from adaos.apps.api import node_api
from adaos.services.scenario.webspace_runtime import WebspaceInfo


def test_desktop_catalog_does_not_scan_application_sources_on_api_event_loop(monkeypatch):
    owner = threading.get_ident()
    calls = []

    class Catalog:
        def list(self, *, mode):
            calls.append(threading.get_ident())
            # Cold runtime selection may hold a single-flight lock in another
            # worker. Waiting for it on the owner loop stalls unrelated HTTP/WS.
            assert threading.get_ident() != owner, "catalog scan blocks API owner loop"
            assert mode == "mixed"
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
