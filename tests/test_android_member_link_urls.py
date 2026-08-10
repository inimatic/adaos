from __future__ import annotations

import importlib.util
import json
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
