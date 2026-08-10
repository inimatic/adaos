from __future__ import annotations

import importlib.util
import json
import queue
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/adaos/integrations/android-node/app/src/main/python/adaos/android/member_link.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("adaos_android_member_link_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_inimatic_root_is_canonicalized_to_https() -> None:
    module = _load_module()

    assert module._canonical_root_url("http://ru.api.inimatic.com/") == (
        "https://ru.api.inimatic.com"
    )


def test_local_development_root_keeps_http() -> None:
    module = _load_module()

    assert module._canonical_root_url("http://127.0.0.1:18778/") == (
        "http://127.0.0.1:18778"
    )


def test_https_join_rejects_root_protocol_downgrade() -> None:
    module = _load_module()

    assert module._joined_hub_url(
        "https://ru.api.inimatic.com",
        "http://ru.api.inimatic.com/hubs/sn_test",
    ) == "https://ru.api.inimatic.com/hubs/sn_test"


def test_invalid_root_url_is_rejected() -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="member_root_url_invalid"):
        module._canonical_root_url("ru.api.inimatic.com")


def test_existing_public_plaintext_config_is_migrated_without_rejoin(
    tmp_path: Path,
) -> None:
    module = _load_module()
    path = tmp_path / "android-member-link.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "hub_url": "http://ru.api.inimatic.com/hubs/sn_test",
                "subnet_id": "sn_test",
                "token": "signed-member-token",
            }
        ),
        encoding="utf-8",
    )

    link = module.AndroidMemberLink(
        tmp_path,
        node_id="android-test",
        local_subnet_id="local-test",
        status_provider=lambda: {},
        document_provider=lambda: {},
        apply_yjs_update=lambda _update: True,
        state_changed=lambda _state: None,
    )

    assert link.snapshot()["hub_url"] == "https://ru.api.inimatic.com/hubs/sn_test"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["root_url"] == "https://ru.api.inimatic.com"


def test_connected_snapshot_requires_fresh_hub_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    link = module.AndroidMemberLink(
        tmp_path,
        node_id="android-test",
        local_subnet_id="local-test",
        status_provider=lambda: {},
        document_provider=lambda: {},
        apply_yjs_update=lambda _update: True,
        state_changed=lambda _state: None,
    )
    link._connected = True
    link._state = "connected"
    link._hello_ack_ok = True
    link._hello_ack_at = 100.0
    link._last_message_at = 100.0

    monkeypatch.setattr(module.time, "time", lambda: 120.0)

    snapshot = link.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["state"] == "recovering"
    assert snapshot["last_error"] == "hub_activity_timeout"


def test_pong_refreshes_member_link_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    link = module.AndroidMemberLink(
        tmp_path,
        node_id="android-test",
        local_subnet_id="local-test",
        status_provider=lambda: {},
        document_provider=lambda: {},
        apply_yjs_update=lambda _update: True,
        state_changed=lambda _state: None,
    )
    monkeypatch.setattr(module.time, "time", lambda: 123.0)

    link._handle_message(object(), {"t": "pong"})

    assert link._last_message_at == 123.0
    assert link._last_pong_at == 123.0


def test_local_yjs_change_queues_semantic_node_state_not_raw_update(
    tmp_path: Path,
) -> None:
    module = _load_module()
    link = module.AndroidMemberLink(
        tmp_path,
        node_id="android-test",
        local_subnet_id="local-test",
        status_provider=lambda: {},
        document_provider=lambda: {"schema": "test"},
        apply_yjs_update=lambda _update: True,
        state_changed=lambda _state: None,
    )
    link._config = {"enabled": True}

    assert link.send_yjs_update(b"local-update") is True
    queued = link._outbound.get_nowait()
    assert queued["t"] == "_node_state.refresh"
    with pytest.raises(queue.Empty):
        link._outbound.get_nowait()
