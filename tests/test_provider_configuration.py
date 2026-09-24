from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.applications.setup import project_setup_state
from adaos.services.providers.configuration import (
    ProviderConfigurationError,
    ProviderConfigurationService,
)
from adaos.domain.application_setup import ApplicationSetupContract


class Vault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, *, default=None):
        return self.values.get(key, default)

    def put(self, key: str, value: str, *, meta=None) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def test_google_provider_configuration_is_revisioned_and_secret_redacted(
    tmp_path: Path,
) -> None:
    vault = Vault()
    service = ProviderConfigurationService(tmp_path, vault)

    missing = service.inspect("google.gmail")
    assert missing["status"] == "missing"
    assert missing["revision"] == 0
    assert missing["missing_fields"] == ["client_id", "client_secret"]

    ready = service.configure(
        "google.gmail",
        {"client_id": "client-id", "client_secret": "client-secret"},
        expected_revision=0,
    )
    assert ready["status"] == "ready"
    assert ready["revision"] == 1
    assert ready["present_fields"] == ["client_id", "client_secret"]
    assert "client-id" not in str(ready)
    assert "client-secret" not in str(ready)
    assert vault.values["provider:google.oauth:client_id"] == "client-id"
    assert vault.values["provider:google.oauth:client_secret"] == "client-secret"

    with pytest.raises(ProviderConfigurationError, match="expected revision"):
        service.configure(
            "google.gmail", {"client_id": "other"}, expected_revision=0
        )


def test_provider_readiness_precedes_connected_account_readiness() -> None:
    contract = ApplicationSetupContract(
        {
            "schema": "adaos.application.setup_contract.v1",
            "application_id": "mail_client",
            "release_digest": "sha256:" + "a" * 64,
            "version": 1,
            "components": [],
            "connected_accounts": [
                {
                    "id": "google.gmail",
                    "title": "Gmail account",
                    "purpose": "Read mail",
                    "required": True,
                    "scopes": ["gmail.modify"],
                }
            ],
            "permissions": [],
            "placement": {"required": False, "title": "Runtime placement"},
            "verification": [],
        }
    )

    state = project_setup_state(
        contract,
        channel="beta",
        provider_configuration_status={"google.gmail": "missing"},
        connected_account_status={"google.gmail": "missing"},
        placement_status="not_applicable",
    )

    provider = next(
        item
        for item in state["requirements"]
        if item["kind"] == "provider_configuration"
    )
    assert provider["status"] == "missing"
    assert provider["action"] == "configure_provider"
    assert state["status"] == "action_required"
