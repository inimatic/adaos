from __future__ import annotations

import requests

from adaos.sdk.net import external_api


class _Response:
    def __init__(self, status_code: int = 200, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}
        self.headers = headers or {}

    def json(self):
        return dict(self._payload)


def test_external_api_falls_back_to_global_proxy_and_records_policy(monkeypatch):
    memory: dict[str, object] = {}
    posts: list[tuple[str, dict]] = []

    def _local_request(*_args, **_kwargs):
        raise requests.exceptions.ConnectTimeout("local connect timed out")

    def _proxy_post(url, *, json=None, **_kwargs):
        posts.append((url, dict(json or {})))
        return _Response()

    monkeypatch.setattr(external_api, "memory_get", lambda key, default=None: memory.get(key, default))
    monkeypatch.setattr(external_api, "memory_set", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(external_api.requests, "request", _local_request)
    monkeypatch.setattr(external_api.requests, "post", _proxy_post)

    result = external_api.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": 55.75},
        service="weather.open_meteo.forecast",
        global_proxy_url="https://api.inimatic.com/v1/external-api/proxy",
    )

    assert result.ok is True
    assert result.mode == "global_proxy"
    assert [attempt["mode"] for attempt in result.attempts] == ["local", "zone_proxy", "global_proxy"]
    assert posts == [
        (
            "https://api.inimatic.com/v1/external-api/proxy",
            {
                "method": "GET",
                "url": "https://api.open-meteo.com/v1/forecast",
                "params": {"latitude": 55.75},
            },
        )
    ]
    policy = next(value for key, value in memory.items() if key.startswith("external_api.channel."))
    assert isinstance(policy, dict)
    assert policy["mode"] == "global_proxy"
    assert policy["local_ok"] is False


def test_external_api_rechecks_local_after_interval(monkeypatch):
    memory: dict[str, object] = {}
    now = 10_000.0

    monkeypatch.setattr(external_api.time, "time", lambda: now)
    monkeypatch.setattr(external_api, "memory_get", lambda key, default=None: memory.get(key, default))
    monkeypatch.setattr(external_api, "memory_set", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(external_api.requests, "request", lambda *_args, **_kwargs: _Response())

    result = external_api.get(
        "https://api.open-meteo.com/v1/forecast",
        service="weather.open_meteo.forecast",
        recheck_interval_s=7,
    )
    assert result.mode == "local"

    policy_key = next(key for key in memory if key.startswith("external_api.channel."))
    policy = dict(memory[policy_key])  # type: ignore[arg-type]
    policy.update({"mode": "global_proxy", "local_ok": False, "last_local_probe_at": now - 8})
    memory[policy_key] = policy

    result = external_api.get(
        "https://api.open-meteo.com/v1/forecast",
        service="weather.open_meteo.forecast",
        recheck_interval_s=7,
    )

    assert result.mode == "local"
    assert result.attempts[0]["mode"] == "local"


def test_external_api_treats_proxy_auth_response_as_channel_failure(monkeypatch):
    memory: dict[str, object] = {}

    def _local_request(*_args, **_kwargs):
        raise requests.exceptions.ConnectTimeout("local connect timed out")

    def _proxy_post(*_args, **_kwargs):
        return _Response(status_code=401, payload={"error": "client_certificate_required"})

    monkeypatch.setattr(external_api, "memory_get", lambda key, default=None: memory.get(key, default))
    monkeypatch.setattr(external_api, "memory_set", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(external_api.requests, "request", _local_request)
    monkeypatch.setattr(external_api.requests, "post", _proxy_post)

    result = external_api.get(
        "https://api.open-meteo.com/v1/forecast",
        service="weather.open_meteo.forecast",
        global_proxy_url="https://api.inimatic.com/v1/external-api/proxy",
    )

    assert result.ok is False
    assert result.response is None
    assert result.error == "global_proxy_proxy_http_401"
    assert [attempt["mode"] for attempt in result.attempts] == ["local", "zone_proxy", "global_proxy"]
