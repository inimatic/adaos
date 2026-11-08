from __future__ import annotations

import json

import pytest

from adaos.services.public_site_delivery_telemetry import (
    PublicSiteDeliveryTelemetryService,
    normalize_delivery_observation,
)


DIGEST = "sha256:" + "a" * 64


def observation(**updates):
    value = {
        "schema": "adaos.public_site_delivery_observation.v1",
        "observed_at": "2026-11-06T12:00:00+00:00",
        "kind": "artifact",
        "status": "healthy",
        "bootstrap": "firebase",
        "descriptor_generation": 12,
        "selected_zone": "cloudflare-global",
        "fallback_zone": None,
        "provider": "cloudflare",
        "cache_state": "hit",
        "site_id": "adaos",
        "deployment_digest": DIGEST,
        "request_count": 4,
        "bytes_delivered": 1024,
        "latency_ms": 42,
    }
    value.update(updates)
    return value


def test_delivery_telemetry_aggregates_only_identity_free_dimensions(tmp_path) -> None:
    service = PublicSiteDeliveryTelemetryService(state_dir=tmp_path, max_events=100)
    first = service.record(
        observation(), source="browser_probe", observation_id="probe-one"
    )
    duplicate = service.record(
        observation(), source="browser_probe", observation_id="probe-one"
    )
    service.record(
        observation(
            observed_at="2026-11-06T12:01:00+00:00",
            status="fallback",
            selected_zone="cloudflare-global",
            fallback_zone="regru-ru",
            provider="regru",
            cache_state="miss",
            request_count=2,
            bytes_delivered=512,
            latency_ms=120,
        ),
        source="browser_probe",
        observation_id="probe-two",
    )

    assert duplicate == first
    status = service.status(site_id="adaos", deployment_digest=DIGEST)
    assert status["privacy"]["visitor_identity"] == "not_collected"
    assert status["totals"] == {
        "observations": 2,
        "request_count": 6,
        "bytes_delivered": 1536,
        "fallback_observations": 1,
        "unavailable_observations": 0,
    }
    assert len(status["aggregates"]) == 2
    encoded = json.dumps(status)
    assert "browser_probe" not in encoded
    assert "visitor" in encoded  # The explicit privacy statement, never an identity.


@pytest.mark.parametrize(
    "field",
    ["visitor_id", "client_ip", "user_agent", "session", "cookie"],
)
def test_delivery_telemetry_rejects_visitor_identity(field) -> None:
    with pytest.raises(ValueError, match="identifying"):
        normalize_delivery_observation({**observation(), field: "secret"})


def test_delivery_telemetry_requires_bounded_fallback_identity() -> None:
    with pytest.raises(ValueError, match="fallback zone"):
        normalize_delivery_observation(observation(status="fallback"))


def test_delivery_telemetry_observation_ids_are_idempotent(tmp_path) -> None:
    service = PublicSiteDeliveryTelemetryService(state_dir=tmp_path)
    service.record(observation(), source="regional_probe", observation_id="probe-one")
    with pytest.raises(ValueError, match="id conflict"):
        service.record(
            observation(bytes_delivered=2048),
            source="regional_probe",
            observation_id="probe-one",
        )
