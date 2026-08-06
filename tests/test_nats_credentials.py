from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from adaos.services.bootstrap_runtime import nats_credentials
from adaos.services.bootstrap_runtime.nats_credentials import NatsCredentialService


def test_credentials_read_normalizes_requested_websocket_transport(monkeypatch) -> None:
    saved: list[dict[str, str | None]] = []
    monkeypatch.setenv("HUB_NATS_TRANSPORT", "ws")
    monkeypatch.setattr(
        nats_credentials,
        "load_nats_runtime_config",
        lambda: {"ws_url": "nats://root.example:4222", "user": "hub", "pass": "secret"},
    )
    monkeypatch.setattr(
        nats_credentials,
        "save_nats_runtime_config",
        lambda **values: saved.append(values),
    )
    policy = SimpleNamespace(
        public_ws_candidates=lambda url: ["wss://root.example/nats"],
        normalize_ws_url=lambda url: url,
    )
    runtime = NatsCredentialService(
        SimpleNamespace(_nats_policy=policy),
        hub_id="sn_local",
    )

    assert runtime.read() == ("wss://root.example/nats", "hub", "secret")
    assert saved == [
        {
            "ws_url": "wss://root.example/nats",
            "user": "hub",
            "password": "secret",
        }
    ]


@pytest.mark.asyncio
async def test_credentials_fetch_is_rate_limited_by_owner() -> None:
    runtime = NatsCredentialService(
        SimpleNamespace(_nats_policy=SimpleNamespace()),
        hub_id="sn_local",
    )
    runtime._last_fetch_at = time.monotonic()

    assert await runtime.fetch() is False
    assert runtime.hub_id == "sn_local"
