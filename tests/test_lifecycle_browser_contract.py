"""Keep the lifecycle acceptance driver on the supported navigation owner."""
from pathlib import Path


def test_lifecycle_browser_uses_one_local_router_location_and_live_projection():
    source = (Path(__file__).resolve().parents[1] /
              "e2e/stand/browser/application-core-lifecycle.mjs").read_text(encoding="utf-8")
    assert "scenario_id: test.scenario" in source
    assert "intent: 'webspace.open'" not in source
    assert "getMaterializationSnapshot" in source
    assert "getRenderMaterializationSnapshot?.()" not in source.split("row.stage = 'core-modal'")[0]
    assert "/materialization?" not in source
    assert "request_source: 'e2e.core.lifecycle'" not in source
