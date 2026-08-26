from __future__ import annotations

import json
from types import SimpleNamespace

from adaos.services import economic_policy
from adaos.services.root import control_lifecycle_sync


def _config(*, zone_id: str = "ru", root_base: str = "https://ru.api.inimatic.com"):
    return SimpleNamespace(
        node_id="node-test",
        subnet_id="sn_test",
        role="hub",
        zone_id=zone_id,
        root_settings=SimpleNamespace(base_url=root_base),
    )


def test_default_economic_status_observes_missing_entitlement(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(economic_policy, "load_config", lambda: _config())
    monkeypatch.setattr(economic_policy, "current_base_dir", lambda: tmp_path)
    monkeypatch.delenv("ADAOS_ECONOMIC_ENTITLEMENT_SNAPSHOT", raising=False)
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)
    monkeypatch.delenv("ADAOS_ROOT_ZONE", raising=False)

    status = economic_policy.current_subnet_economic_status()

    assert status["schema"] == "adaos.subnet.economic_status.v1"
    assert status["zone_id"] == "ru"
    assert status["subscription_state"] == "unassigned"
    assert status["entitlement_state"] == "disabled_observed"
    assert status["enforcement_mode"] == "observe"
    assert status["disabled_resource_count"] == len(economic_policy.ROOT_GOVERNED_RESOURCES)
    assert status["management_authority"]["global_base_url"] == "https://api.inimatic.com"
    assert status["management_authority"]["cross_zone_expected"] is True


def test_economic_status_reads_root_entitlement_snapshot(monkeypatch, tmp_path) -> None:
    snapshot_path = tmp_path / "entitlement.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "mode": "observe",
                "subscription": {"state": "active", "plan_id": "developer"},
                "entitlement": {
                    "state": "limited_observed",
                    "disabled_resources": [
                        {
                            "resource": "media.indexing",
                            "reason_code": "resource_not_in_plan",
                            "reason": "Resource is not included in plan",
                            "source": "plan",
                        }
                    ],
                },
                "usage": {"llm.requests": {"used_24h": 3}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(economic_policy, "load_config", lambda: _config())
    monkeypatch.setenv("ADAOS_ECONOMIC_ENTITLEMENT_SNAPSHOT", str(snapshot_path))
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)

    status = economic_policy.current_subnet_economic_status()
    compact = economic_policy.compact_economic_status_for_control_report()

    assert status["source"] == "root_entitlement_snapshot"
    assert status["subscription_state"] == "active"
    assert status["plan_id"] == "developer"
    assert status["disabled_resource_count"] == 1
    assert compact["usage"]["llm.requests"]["used_24h"] == 3


def test_control_lifecycle_zone_uses_node_config(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_ROOT_ZONE", raising=False)
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)

    assert control_lifecycle_sync._zone(_config(zone_id="ru")) == "ru"
