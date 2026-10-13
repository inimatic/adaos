from __future__ import annotations

import asyncio
import importlib
import json
import threading
from typing import Any

import pytest


def _load_bridge(monkeypatch: pytest.MonkeyPatch, **environment: str):
    from adaos.services.yjs import sidecar_shadow

    sidecar_shadow.unregister_sidecar_shadow_mirror()
    defaults = {
        "ADAOS_YJS_SIDECAR_SHADOW_ENABLE": "1",
        "ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES": "default",
        "ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT": "8",
        "ADAOS_YJS_SIDECAR_SHADOW_TIMEOUT_S": "1",
        "ADAOS_TOKEN": "test-token",
    }
    defaults.update(environment)
    for name, value in defaults.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(sidecar_shadow)


def test_shadow_bridge_queues_allowlisted_updates_without_taking_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _load_bridge(monkeypatch)
    accepted: list[tuple[str, bytes, int]] = []
    monkeypatch.setattr(
        bridge,
        "_post_update",
        lambda webspace, update, sequence: accepted.append((webspace, update, sequence)),
    )

    async def exercise() -> dict[str, Any]:
        bridge._mirror_write_listener("other", b"ignored")
        bridge._mirror_write_listener("default", b"update-one")
        await bridge.wait_for_sidecar_shadow_idle(2)
        return bridge.sidecar_shadow_mirror_status()

    status = asyncio.run(exercise())
    assert accepted == [("default", b"update-one", 1)]
    assert status["authority"] == "python"
    assert status["accepted_total"] == 1
    assert status["dropped_total"] == 0
    bridge.unregister_sidecar_shadow_mirror()


def test_shadow_bridge_posts_bounded_binary_control_request(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge(monkeypatch)
    captured: dict[str, Any] = {}

    class _Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps({"accepted": True}).encode()

    def _urlopen(request, *, timeout: float):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _urlopen)
    bridge._post_update("default", b"binary-update", 7)

    assert captured["url"] == "http://127.0.0.1:7426/v1/yjs-shadow/update"
    assert captured["data"] == b"binary-update"
    assert captured["headers"]["x-adaos-token"] == "test-token"
    assert captured["headers"]["x-adaos-webspace-id"] == "default"
    assert captured["headers"]["x-adaos-yjs-sequence"] == "7"
    assert captured["timeout"] == 1.0
    bridge.unregister_sidecar_shadow_mirror()


def test_shadow_bridge_rejects_non_loopback_control_url(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge(
        monkeypatch,
        ADAOS_YJS_SIDECAR_SHADOW_CONTROL_URL="https://example.test/v1/yjs-shadow/update",
    )
    status = bridge.sidecar_shadow_mirror_status()
    assert bridge.register_sidecar_shadow_mirror() is False
    assert "loopback HTTP" in str(status["last_error"])
    bridge.unregister_sidecar_shadow_mirror()


def test_shadow_bridge_preserves_one_queue_across_writer_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge(monkeypatch)
    accepted: list[tuple[str, int]] = []
    accepted_lock = threading.Lock()

    def _accept(webspace: str, _update: bytes, sequence: int) -> None:
        with accepted_lock:
            accepted.append((webspace, sequence))

    monkeypatch.setattr(bridge, "_post_update", _accept)
    writers = [
        threading.Thread(
            target=bridge._mirror_write_listener,
            args=("default", f"update-{index}".encode()),
        )
        for index in range(6)
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    asyncio.run(bridge.wait_for_sidecar_shadow_idle(2))
    assert len(accepted) == 6
    assert sorted(sequence for _webspace, sequence in accepted) == list(range(1, 7))
    assert bridge.sidecar_shadow_mirror_status()["dropped_total"] == 0
    bridge.unregister_sidecar_shadow_mirror()
