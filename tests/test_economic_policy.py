from __future__ import annotations

import json
import time
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
                "usage": {
                    "llm.requests": {"used_24h": 3},
                    "codex.api.tokens": {"used_30d": 1000, "quota_remaining": 9000},
                },
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
    assert compact["usage"]["codex.api.tokens"]["quota_remaining"] == 9000


def test_economic_status_accepts_root_entitlement_api_wrapper(monkeypatch, tmp_path) -> None:
    snapshot_path = tmp_path / "entitlement-wrapper.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ok": True,
                "subnet_id": "sn_test",
                "entitlement": {
                    "schema": "adaos.root_mgmnt.economic_entitlement.v1",
                    "mode": "observe",
                    "subscription": {"state": "active", "plan_id": "builder"},
                    "entitlement": {"state": "enabled", "disabled_resources": []},
                    "usage": {"llm.requests": {"used_24h": 7}},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(economic_policy, "load_config", lambda: _config())
    monkeypatch.setenv("ADAOS_ECONOMIC_ENTITLEMENT_SNAPSHOT", str(snapshot_path))
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)

    status = economic_policy.current_subnet_economic_status()

    assert status["subscription_state"] == "active"
    assert status["plan_id"] == "builder"
    assert status["entitlement_state"] == "enabled"
    assert status["usage"]["llm.requests"]["used_24h"] == 7


def test_refresh_entitlement_snapshot_from_root(monkeypatch, tmp_path) -> None:
    class FakeRootClient:
        base_url = "https://ru.api.inimatic.com"

        def request(self, method, path, **kwargs):
            assert method == "GET"
            assert path == "/v1/hub/economic/entitlement"
            assert kwargs["timeout"] == 4
            return {
                "ok": True,
                "subnet_id": "sn_test",
                "entitlement": {
                    "schema": "adaos.root_mgmnt.economic_entitlement.v1",
                    "mode": "observe",
                    "subscription": {"state": "active", "plan_id": "builder"},
                    "entitlement": {"state": "enabled", "disabled_resources": []},
                    "usage": {
                        "llm.requests": {"used_24h": 2, "quota_remaining": 19998},
                        "codex.api.tokens": {"used_30d": 100, "quota_remaining": 19999900},
                    },
                },
            }

    monkeypatch.setattr(economic_policy, "load_config", lambda: _config())
    monkeypatch.setattr(economic_policy, "current_base_dir", lambda: tmp_path)
    monkeypatch.setattr(
        economic_policy,
        "_economic_root_http_client",
        lambda conf, *, base_dir, root_base_url=None: FakeRootClient(),
    )
    monkeypatch.delenv("ADAOS_ECONOMIC_ENTITLEMENT_SNAPSHOT", raising=False)
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)

    refresh = economic_policy.refresh_entitlement_snapshot_from_root(timeout=4)
    status = economic_policy.current_subnet_economic_status()

    assert refresh["ok"] is True
    assert refresh["plan_id"] == "builder"
    assert status["source"] == "root_entitlement_snapshot"
    assert status["subscription_state"] == "active"
    assert status["usage"]["codex.api.tokens"]["quota_remaining"] == 19999900


def test_economic_status_observes_nlu_teacher_llm_usage(monkeypatch, tmp_path) -> None:
    state_dir = tmp_path / "state"
    teacher_dir = state_dir / "skills" / "nlu_teacher"
    teacher_dir.mkdir(parents=True)
    now = time.time()
    (teacher_dir / "desktop.json").write_text(
        json.dumps(
            {
                "llm_logs": [
                    {"id": "llm.recent", "ts": now - 60, "status": "succeeded", "model": "gpt-4o-mini"},
                    {"id": "llm.week-old", "ts": now - 8 * 24 * 60 * 60, "status": "error", "model": "gpt-4o-mini"},
                    {"id": "llm.old", "ts": now - 40 * 24 * 60 * 60, "status": "succeeded", "model": "gpt-4o-mini"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(economic_policy, "load_config", lambda: _config())
    monkeypatch.setattr(economic_policy, "current_base_dir", lambda: tmp_path)
    monkeypatch.setattr(economic_policy, "current_state_dir", lambda: state_dir)
    monkeypatch.delenv("ADAOS_ECONOMIC_ENTITLEMENT_SNAPSHOT", raising=False)
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)

    status = economic_policy.current_subnet_economic_status()
    compact = economic_policy.compact_economic_status_for_control_report()

    usage = status["usage"]["llm.requests"]
    assert usage["used_24h"] == 1
    assert usage["used_7d"] == 1
    assert usage["used_30d"] == 2
    assert usage["last_model"] == "gpt-4o-mini"
    assert usage["sources"] == ["nlu_teacher.llm_logs"]
    assert compact["usage"]["llm.requests"]["used_24h"] == 1


def test_control_lifecycle_zone_uses_node_config(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_ROOT_ZONE", raising=False)
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)

    assert control_lifecycle_sync._zone(_config(zone_id="ru")) == "ru"
