from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import io_webhooks


def test_pair_create_uses_canonical_json_body(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _issue_pair_code(*, bot_id: str, hub_id: str | None, ttl_sec: int):
        captured.update(bot_id=bot_id, hub_id=hub_id, ttl_sec=ttl_sec)
        return {"pair_code": "ABC123", "expires_at": 1234}

    monkeypatch.setattr(io_webhooks.pairing_svc, "issue_pair_code", _issue_pair_code)

    result = asyncio.run(
        io_webhooks.tg_pair_create(
            io_webhooks.TelegramPairCreateRequest(
                hub_id="sn_local",
                bot_id="builder-bot",
                ttl=900,
            )
        )
    )

    assert captured == {
        "bot_id": "builder-bot",
        "hub_id": "sn_local",
        "ttl_sec": 900,
    }
    assert result["hub_id"] == "sn_local"
    assert result["bot_id"] == "builder-bot"


def test_pair_create_http_route_parses_cli_json_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _issue_pair_code(*, bot_id: str, hub_id: str | None, ttl_sec: int):
        captured.update(bot_id=bot_id, hub_id=hub_id, ttl_sec=ttl_sec)
        return {"pair_code": "HTTP123", "expires_at": 9012}

    monkeypatch.setattr(io_webhooks.pairing_svc, "issue_pair_code", _issue_pair_code)
    app = FastAPI()
    app.include_router(io_webhooks.router)

    with TestClient(app) as client:
        response = client.post(
            "/io/tg/pair/create",
            json={"code": "PING", "hub_id": "sn_http", "ttl": 750},
        )

    assert response.status_code == 200
    assert captured == {
        "bot_id": "main-bot",
        "hub_id": "sn_http",
        "ttl_sec": 750,
    }
    assert response.json()["hub_id"] == "sn_http"


def test_pair_create_retains_legacy_query_parameters(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _issue_pair_code(*, bot_id: str, hub_id: str | None, ttl_sec: int):
        captured.update(bot_id=bot_id, hub_id=hub_id, ttl_sec=ttl_sec)
        return {"pair_code": "XYZ789", "expires_at": 5678}

    monkeypatch.setattr(io_webhooks.pairing_svc, "issue_pair_code", _issue_pair_code)

    result = asyncio.run(
        io_webhooks.tg_pair_create(
            hub="legacy-hub",
            ttl=300,
            bot="legacy-bot",
        )
    )

    assert captured == {
        "bot_id": "legacy-bot",
        "hub_id": "legacy-hub",
        "ttl_sec": 300,
    }
    assert result["pair_code"] == "XYZ789"
