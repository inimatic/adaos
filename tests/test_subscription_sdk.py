from __future__ import annotations

from adaos.sdk import subscriptions
from adaos.services import economic_policy


def test_codex_usage_snapshot_projects_bounded_24h_usage(monkeypatch) -> None:
    monkeypatch.setattr(
        economic_policy,
        "current_subnet_economic_status",
        lambda: {
            "generated_at": "2026-09-03T04:00:00Z",
            "entitlement_snapshot": {"loaded": True, "updated_at": "2026-09-03T03:59:00Z"},
            "usage": {
                "codex.api.tokens": {
                    "used_24h": 24_092,
                    "quota_remaining": 19_975_908,
                    "quota_limit": 20_000_000,
                    "accuracy": "provider_reported",
                    "metering": "codex_usage_stream",
                    "last_seen_at": "2026-09-03T03:47:22Z",
                    "usage_breakdown": {
                        "window_24h": {
                            "fresh_plus_output_tokens": 13_212,
                            "cached_input_tokens": 10_880,
                            "output_tokens": 506,
                            "runs": 1,
                        }
                    },
                }
            },
        },
    )

    snapshot = subscriptions.get_codex_usage_snapshot(webspace_id="desktop")

    assert snapshot == {
        "schema": "adaos.sdk.subscription.codex_usage.v1",
        "status": "ready",
        "resource": "codex.api.tokens",
        "period": "24h",
        "used_tokens": 24_092,
        "remaining_tokens": 19_975_908,
        "limit_tokens": 20_000_000,
        "fresh_plus_output_tokens": 13_212,
        "cached_input_tokens": 10_880,
        "output_tokens": 506,
        "runs": 1,
        "accuracy": "provider_reported",
        "metering": "codex_usage_stream",
        "updated_at": "2026-09-03T03:47:22Z",
        "webspace_id": "desktop",
        "reason": None,
    }


def test_codex_usage_refresh_failure_returns_stale_cached_data(monkeypatch) -> None:
    def fail_refresh(*, timeout: float):
        assert timeout == 2.0
        raise TimeoutError("root unavailable")

    monkeypatch.setattr(economic_policy, "refresh_entitlement_snapshot_from_root", fail_refresh)
    monkeypatch.setattr(
        economic_policy,
        "current_subnet_economic_status",
        lambda: {
            "generated_at": "2026-09-03T04:00:00Z",
            "entitlement_snapshot": {"loaded": True},
            "usage": {"codex.api.tokens": {"used_24h": 100, "quota_remaining": 900}},
        },
    )

    snapshot = subscriptions.get_codex_usage_model(refresh=True, timeout=2.0)

    assert snapshot.status == "stale"
    assert snapshot.used_tokens == 100
    assert snapshot.remaining_tokens == 900
    assert snapshot.reason == "TimeoutError: root unavailable"


def test_codex_usage_snapshot_is_unavailable_without_metering(monkeypatch) -> None:
    monkeypatch.setattr(
        economic_policy,
        "current_subnet_economic_status",
        lambda: {
            "generated_at": "2026-09-03T04:00:00Z",
            "entitlement_snapshot": {"loaded": False},
            "usage": {},
        },
    )

    snapshot = subscriptions.get_codex_usage_model(webspace_id="living-room")

    assert snapshot.status == "unavailable"
    assert snapshot.used_tokens is None
    assert snapshot.remaining_tokens is None
    assert snapshot.webspace_id == "living-room"
    assert snapshot.reason == "codex_usage_not_metered"


def test_sdk_export_discovers_subscription_usage_contract() -> None:
    from adaos.sdk.core.exporter import export

    metadata = export(level="std", query="Codex token usage remaining subscription", limit=8)
    names = {item["name"] for item in metadata["tools"]}

    assert "adaos.sdk.subscriptions.get_codex_usage_snapshot" in names
    assert "adaos.sdk.subscriptions.get_codex_usage_model" in names
