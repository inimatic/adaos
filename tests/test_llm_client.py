from __future__ import annotations

from types import SimpleNamespace

import pytest


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ADAOS_LLM_ENDPOINT",
        "ADAOS_LLM_MODELS_ENDPOINT",
        "ADAOS_ROOT_LLM_BASE_URL",
        "ADAOS_LLM_ROOT_BASE_URL",
        "ADAOS_ROOT_LLM_FALLBACK_BASE_URLS",
        "ADAOS_ROOT_VERIFY_CA",
        "ADAOS_LLM_ROOT_VERIFY_CA",
        "ADAOS_SUBNET_ID",
        "ADAOS_NODE_ID",
    ):
        monkeypatch.delenv(key, raising=False)


def test_send_response_uses_root_proxy_with_node_identity(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from adaos.sdk.llm import llm_client as llm

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("ADAOS_ROOT_VERIFY_CA", "1")

    ca_path = tmp_path / "ca.cert"
    cert_path = tmp_path / "hub_cert.pem"
    key_path = tmp_path / "hub_private.pem"
    for path in (ca_path, cert_path, key_path):
        path.write_text("test", encoding="utf-8")

    class FakeConfig:
        root_settings = SimpleNamespace(base_url="https://ru.api.inimatic.com")
        subnet_id = "sn_test"
        node_id = "node_test"

        def ca_cert_path(self):
            return ca_path

        def hub_cert_path(self):
            return cert_path

        def hub_key_path(self):
            return key_path

    fake_ctx = SimpleNamespace(
        settings=SimpleNamespace(api_base="http://127.0.0.1:8777", subnet_id="sn_settings"),
        config=FakeConfig(),
    )
    clients: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []

    class FakeRootHttpClient:
        def __init__(self, base_url, verify=True, cert=None, default_headers=None):
            self.base_url = base_url
            self.verify = verify
            self.cert = cert
            self.default_headers = dict(default_headers or {})
            clients.append({"base_url": base_url, "verify": verify, "cert": cert})

        def request(self, method, path, **kwargs):
            requests.append({"base_url": self.base_url, "method": method, "path": path, "kwargs": kwargs})
            return {"output": [{"content": [{"type": "output_text", "text": "Paris is in France."}]}]}

    monkeypatch.setattr(llm, "_current_ctx", lambda: fake_ctx)
    monkeypatch.setattr(llm, "RootHttpClient", FakeRootHttpClient)

    result = llm.send_response(
        [
            {"role": "system", "content": "Answer facts briefly."},
            {"role": "user", "content": "What is France known for?"},
            {"role": "assistant", "content": "France is known for Paris, culture, and food."},
            {"role": "user", "content": "Where is Paris?"},
        ],
        model="gpt-test",
        max_tokens=40,
        request_id="req.test",
    )

    assert result["output_text"] == "Paris is in France."
    assert clients[0] == {
        "base_url": "https://ru.api.inimatic.com",
        "verify": str(ca_path),
        "cert": (str(cert_path), str(key_path)),
    }
    assert requests[0]["path"] == "/v1/llm/response"
    kwargs = requests[0]["kwargs"]  # type: ignore[index]
    assert kwargs["headers"] == {"X-AdaOS-Subnet-Id": "sn_test", "X-AdaOS-Node-Id": "node_test"}
    body = kwargs["json"]  # type: ignore[index]
    assert body["model"] == "gpt-test"
    assert body["request_id"] == "req.test"
    assert body["instructions"] == "Answer facts briefly."
    assert body["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "What is France known for?"}]},
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "France is known for Paris, culture, and food."}],
        },
        {"role": "user", "content": [{"type": "input_text", "text": "Where is Paris?"}]},
    ]
    assert body["max_output_tokens"] == 40
    assert "max_tokens" not in body
    assert result["_protocol"]["llm_proxy"] == {
        "base_url": "https://ru.api.inimatic.com",
        "fallback": False,
        "attempts": [],
    }


def test_send_response_falls_back_when_zone_proxy_cannot_reach_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from adaos.sdk.llm import llm_client as llm

    _clear_llm_env(monkeypatch)
    fake_ctx = SimpleNamespace(
        settings=SimpleNamespace(api_base="https://ru.api.inimatic.com"),
        config=SimpleNamespace(subnet_id="sn_test", node_id="node_test"),
    )
    calls: list[dict[str, object]] = []

    class FakeRootHttpClient:
        def __init__(self, base_url, verify=True, cert=None, default_headers=None):
            self.base_url = base_url
            self.verify = verify
            self.cert = cert

        def request(self, method, path, **kwargs):
            calls.append({"base_url": self.base_url, "method": method, "path": path, "kwargs": kwargs})
            if self.base_url == "https://ru.api.inimatic.com":
                raise llm.RootHttpError(
                    "unsupported country",
                    status_code=403,
                    payload={"error": {"code": "unsupported_country_region_territory"}},
                )
            return {"output": [{"content": [{"type": "output_text", "text": "fallback ok"}]}]}

    monkeypatch.setattr(llm, "_current_ctx", lambda: fake_ctx)
    monkeypatch.setattr(llm, "RootHttpClient", FakeRootHttpClient)

    result = llm.send_response([{"role": "user", "content": "Return ok."}])

    assert [call["base_url"] for call in calls] == ["https://ru.api.inimatic.com", "https://api.inimatic.com"]
    assert result["output_text"] == "fallback ok"
    assert result["_protocol"]["llm_proxy"] == {
        "base_url": "https://api.inimatic.com",
        "fallback": True,
        "attempts": [{"base_url": "https://ru.api.inimatic.com", "error": "unsupported_country_region_territory"}],
    }


def test_send_response_falls_back_when_zone_proxy_upstream_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from adaos.sdk.llm import llm_client as llm

    _clear_llm_env(monkeypatch)
    fake_ctx = SimpleNamespace(
        settings=SimpleNamespace(api_base="https://ru.api.inimatic.com"),
        config=SimpleNamespace(subnet_id="sn_test", node_id="node_test"),
    )
    calls: list[str] = []

    class FakeRootHttpClient:
        def __init__(self, base_url, verify=True, cert=None, default_headers=None):
            self.base_url = base_url
            self.verify = verify
            self.cert = cert

        def request(self, method, path, **kwargs):
            calls.append(self.base_url)
            if self.base_url == "https://ru.api.inimatic.com":
                raise llm.RootHttpError(
                    "llm_proxy_upstream_failed",
                    status_code=503,
                    error_code="llm_proxy_upstream_failed",
                    payload={"ok": False, "error": "llm_proxy_upstream_failed"},
                )
            return {"output": [{"content": [{"type": "output_text", "text": "global root ok"}]}]}

    monkeypatch.setattr(llm, "_current_ctx", lambda: fake_ctx)
    monkeypatch.setattr(llm, "RootHttpClient", FakeRootHttpClient)

    result = llm.send_response([{"role": "user", "content": "Return ok."}])

    assert calls == ["https://ru.api.inimatic.com", "https://api.inimatic.com"]
    assert result["output_text"] == "global root ok"
    assert result["_protocol"]["llm_proxy"] == {
        "base_url": "https://api.inimatic.com",
        "fallback": True,
        "attempts": [{"base_url": "https://ru.api.inimatic.com", "error": "llm_proxy_upstream_failed"}],
    }


def test_send_response_does_not_fallback_on_root_policy_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    from adaos.sdk.llm import llm_client as llm

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("ADAOS_ROOT_LLM_FALLBACK_BASE_URLS", "https://ru.api.inimatic.com")
    fake_ctx = SimpleNamespace(
        settings=SimpleNamespace(api_base="https://api.inimatic.com"),
        config=SimpleNamespace(subnet_id="sn_test", node_id="node_test"),
    )
    calls: list[str] = []

    class FakeRootHttpClient:
        def __init__(self, base_url, verify=True, cert=None, default_headers=None):
            self.base_url = base_url
            self.verify = verify
            self.cert = cert

        def request(self, method, path, **kwargs):
            calls.append(self.base_url)
            raise llm.RootHttpError(
                "subnet_not_allowed",
                status_code=403,
                error_code="subnet_not_allowed",
                payload={"ok": False, "error": "subnet_not_allowed"},
            )

    monkeypatch.setattr(llm, "_current_ctx", lambda: fake_ctx)
    monkeypatch.setattr(llm, "RootHttpClient", FakeRootHttpClient)

    with pytest.raises(llm.RootHttpError):
        llm.send_response([{"role": "user", "content": "Return ok."}])

    assert calls == ["https://api.inimatic.com"]


def test_response_jobs_submit_and_poll_same_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from adaos.sdk.llm import llm_client as llm

    _clear_llm_env(monkeypatch)
    fake_ctx = SimpleNamespace(
        settings=SimpleNamespace(api_base="https://ru.api.inimatic.com"),
        config=SimpleNamespace(subnet_id="sn_test", node_id="node_test"),
    )
    calls: list[dict[str, object]] = []

    class FakeRootHttpClient:
        def __init__(self, base_url, verify=True, cert=None, default_headers=None):
            self.base_url = base_url
            self.verify = verify
            self.cert = cert

        def request(self, method, path, **kwargs):
            calls.append({"base_url": self.base_url, "method": method, "path": path, "kwargs": kwargs})
            if method == "POST" and path == "/v1/llm/jobs":
                return {
                    "ok": True,
                    "schema": "adaos.root.llm.job.v1",
                    "job_id": "llm_job_test",
                    "request_id": "req.async",
                    "status": "queued",
                }
            if method == "GET" and path == "/v1/llm/jobs/llm_job_test":
                return {
                    "ok": True,
                    "schema": "adaos.root.llm.job.v1",
                    "job_id": "llm_job_test",
                    "request_id": "req.async",
                    "status": "succeeded",
                    "response": {"output": [{"content": [{"type": "output_text", "text": "async ok"}]}]},
                }
            raise AssertionError(f"unexpected request {method} {path}")

    monkeypatch.setattr(llm, "_current_ctx", lambda: fake_ctx)
    monkeypatch.setattr(llm, "RootHttpClient", FakeRootHttpClient)

    submitted = llm.submit_response_job(
        [{"role": "user", "content": "Return ok."}],
        request_id="req.async",
        timeout=3,
    )
    assert submitted["job_id"] == "llm_job_test"
    assert submitted["_client"]["base_url"] == "https://ru.api.inimatic.com"
    assert submitted["_protocol"]["llm_proxy"]["base_url"] == "https://ru.api.inimatic.com"

    polled = llm.get_response_job("llm_job_test", base_url=submitted["_client"]["base_url"])
    assert polled["status"] == "succeeded"
    assert polled["output_text"] == "async ok"
    assert [call["path"] for call in calls] == ["/v1/llm/jobs", "/v1/llm/jobs/llm_job_test"]
