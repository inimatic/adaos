from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import io_webhooks
from adaos.adapters.db import sqlite as sqlite_db


class _Sql:
    def __init__(self, path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


def test_pair_create_uses_canonical_json_body(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _issue_pair_code(*, bot_id: str, hub_id: str | None, ttl_sec: int, webspace_id: str | None = None):
        captured.update(bot_id=bot_id, hub_id=hub_id, ttl_sec=ttl_sec, webspace_id=webspace_id)
        return {"pair_code": "ABC123", "expires_at": 1234}

    monkeypatch.setattr(io_webhooks.pairing_svc, "issue_pair_code", _issue_pair_code)

    result = asyncio.run(
        io_webhooks.tg_pair_create(
            io_webhooks.TelegramPairCreateRequest(
                hub_id="sn_local",
                bot_id="builder-bot",
                ttl=900,
                webspace_id="dev1-dev",
            )
        )
    )

    assert captured == {
        "bot_id": "builder-bot",
        "hub_id": "sn_local",
        "ttl_sec": 900,
        "webspace_id": "dev1-dev",
    }
    assert result["hub_id"] == "sn_local"
    assert result["bot_id"] == "builder-bot"


def test_pair_create_http_route_parses_cli_json_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _issue_pair_code(*, bot_id: str, hub_id: str | None, ttl_sec: int, webspace_id: str | None = None):
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

    async def _issue_pair_code(*, bot_id: str, hub_id: str | None, ttl_sec: int, webspace_id: str | None = None):
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


def test_pairing_schema_migrates_and_persists_webspace_route(monkeypatch, tmp_path) -> None:
    sql = _Sql(tmp_path / "pairing.sqlite3")
    with sql.connect() as con:
        con.execute(
            "CREATE TABLE pair_codes(code TEXT PRIMARY KEY, bot_id TEXT, hub_id TEXT, expires_at INT, state TEXT, created_at INT, note TEXT)"
        )
        con.execute(
            "CREATE TABLE chat_bindings(platform TEXT, user_id TEXT, bot_id TEXT, ada_user_id TEXT, hub_id TEXT, created_at INT, last_seen INT, PRIMARY KEY(platform, user_id, bot_id))"
        )
        con.commit()
    monkeypatch.setattr(sqlite_db, "get_ctx", lambda: SimpleNamespace(sql=sql))

    issued = sqlite_db.pair_issue(
        "main-bot",
        "sn-test",
        ttl_sec=600,
        webspace_id="dev1-dev",
    )
    assert sqlite_db.pair_get(issued["code"])["webspace_id"] == "dev1-dev"

    binding = sqlite_db.binding_upsert(
        "telegram",
        "42",
        "main-bot",
        hub_id="sn-test",
        webspace_id="dev1-dev",
    )
    assert binding["webspace_id"] == "dev1-dev"
