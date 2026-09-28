from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.sdk import automation


class _Context:
    def load_cert_chain(self, cert_file, key_file):
        assert cert_file == "client.crt"
        assert key_file == "client.key"


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


def test_inventory_uses_core_owned_identity_and_returns_sanitized_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "builder-token"
    token_file.write_text("private-builder-token\n", encoding="utf-8")
    checked = []
    monkeypatch.setattr(automation.access, "require", checked.append)
    monkeypatch.setattr(automation.ssl, "create_default_context", lambda **_kw: _Context())
    monkeypatch.setattr(
        automation,
        "merged_runtime_dotenv_env",
        lambda _env: {
            "ADAOS_AUTOMATION_ROOT_URL": "https://ru.api.inimatic.com",
            "ADAOS_AUTOMATION_BUILDER_ID": "manager-desktop",
            "ADAOS_AUTOMATION_BUILDER_TOKEN_FILE": str(token_file),
            "ADAOS_AUTOMATION_CLIENT_CERT": "client.crt",
            "ADAOS_AUTOMATION_CLIENT_KEY": "client.key",
        },
    )
    observed = {}

    def open_request(request, **kwargs):
        observed["url"] = request.full_url
        observed["authorization"] = request.headers["Authorization"]
        observed.update(kwargs)
        return _Response(
            {
                "schema": "adaos.automation.builder_inventory.v1",
                "nodes": [{"node_id": "pod-1"}],
                "tasks": [],
            }
        )

    monkeypatch.setattr(automation.urllib.request, "urlopen", open_request)

    result = automation.inventory(task_limit=999)

    assert checked == ["external_provider.use"]
    assert result["nodes"] == [{"node_id": "pod-1"}]
    assert observed["url"].endswith("/builders/manager-desktop/inventory?task_limit=100")
    assert observed["authorization"] == "Bearer private-builder-token"
    assert "private-builder-token" not in repr(result)


def test_inventory_fails_closed_without_configured_identity(monkeypatch) -> None:
    monkeypatch.setattr(automation.access, "require", lambda _permission: None)
    monkeypatch.setattr(automation, "merged_runtime_dotenv_env", lambda _env: {})
    with pytest.raises(automation.AutomationInventoryUnavailable) as error:
        automation.inventory()
    assert error.value.code == "automation_identity_not_configured"
