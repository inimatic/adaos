from __future__ import annotations

from adaos.services.runtime_topology import (
    DEFAULT_CANDIDATE_RUNTIME_PORT,
    DEFAULT_RUNTIME_PORT,
    DEFAULT_SUPERVISOR_PORT,
    http_base,
    is_loopback_http_url,
    runtime_fallback_http_bases,
    runtime_fallback_ws_bases,
    runtime_probe_http_bases,
    supervisor_base_candidates_from_env,
)


def test_runtime_topology_declares_default_ports() -> None:
    assert DEFAULT_SUPERVISOR_PORT == 8776
    assert DEFAULT_RUNTIME_PORT == 8777
    assert DEFAULT_CANDIDATE_RUNTIME_PORT == 8778


def test_runtime_topology_builds_runtime_fallbacks_in_existing_order() -> None:
    assert runtime_fallback_http_bases(prefer_member=True)[:4] == [
        "http://127.0.0.1:8778",
        "http://localhost:8778",
        "http://127.0.0.1:8777",
        "http://localhost:8777",
    ]
    assert runtime_fallback_ws_bases() == [
        "ws://127.0.0.1:8778",
        "ws://127.0.0.1:8777",
    ]


def test_runtime_topology_honors_runtime_port_env(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8780")

    assert runtime_probe_http_bases()[:2] == [
        "http://127.0.0.1:8780",
        "http://127.0.0.1:8778",
    ]


def test_supervisor_candidates_require_runtime_signal(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.delenv("ADAOS_AUTOSTART_MANAGED", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_URL", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_HOST", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_PORT", raising=False)

    assert supervisor_base_candidates_from_env(require_signal=True) == []

    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    assert supervisor_base_candidates_from_env(require_signal=True)[0] == "http://127.0.0.1:8776"


def test_loopback_detection_accepts_http_and_ws_urls() -> None:
    assert is_loopback_http_url("http://localhost:8777") is True
    assert is_loopback_http_url("ws://127.0.0.1:8778/yws") is True
    assert is_loopback_http_url("https://api.inimatic.com") is False
    assert http_base(port=8777) == "http://127.0.0.1:8777"
