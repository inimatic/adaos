from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import personalization
from adaos.services.agent_context import get_ctx
from adaos.services import access_links


TOKEN_HEADERS = {"X-AdaOS-Token": "dev-local-token"}


@pytest.fixture(autouse=True)
def _disable_root_invite_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOT_TOKEN", raising=False)
    monkeypatch.delenv("ADAOS_ROOT_TOKEN", raising=False)
    monkeypatch.delenv("HUB_ROOT_TOKEN", raising=False)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(personalization.router, prefix="/api")
    return TestClient(app)


def test_phase4_current_user_profile_preferences_and_denied_role_edit() -> None:
    client = _client()

    profile = client.patch(
        "/api/personalization/current-user/profile",
        json={"display_name": "Masha", "language": "ru", "role": "owner"},
        headers=TOKEN_HEADERS,
    )
    assert profile.status_code == 403
    assert profile.json()["detail"]["code"] == "profile_policy_key_denied"

    profile = client.patch(
        "/api/personalization/current-user/profile",
        json={"display_name": "Masha", "language": "ru"},
        headers=TOKEN_HEADERS,
    )
    assert profile.status_code == 200
    assert profile.json()["profile"]["display_name"] == "Masha"

    preferences = client.patch(
        "/api/personalization/current-user/preferences",
        json={"theme": "dark", "ui_density": "compact", "memory_privacy": "local"},
        headers=TOKEN_HEADERS,
    )
    assert preferences.status_code == 200
    assert preferences.json()["preferences"]["theme"] == "dark"

    header = client.get("/api/personalization/current-user/header-settings", headers=TOKEN_HEADERS)
    assert header.status_code == 200
    payload = header.json()["settings"]
    assert payload["display_name"] == "Masha"
    assert payload["theme"] == "dark"
    assert payload["role_status"] == {"value": "owner", "editable": False}
    assert payload["identity_source"] == "owner_settings_fallback"

    decision = client.get(
        "/api/personalization/policy/explain?action=profile.write.self",
        headers=TOKEN_HEADERS,
    )
    assert decision.status_code == 200
    assert decision.json()["decision"]["decision"] == "allow"


def test_phase4_personalization_options_and_current_device_status() -> None:
    client = _client()
    access_links.upsert_link("browser", "device-phase45", {"display_name": "Masha phone"})

    options = client.get("/api/personalization/options", headers=TOKEN_HEADERS)
    assert options.status_code == 200
    payload = options.json()["options"]
    assert [item["value"] for item in payload["languages"]] == ["en", "ru"]
    assert [item["value"] for item in payload["locales"]] == ["en-US", "ru-RU"]
    assert any(item["value"] == "UTC" for item in payload["timezones"])
    assert any(item["kind"] == "workspace" for item in payload["scopes"])

    header = client.get(
        "/api/personalization/current-user/header-settings",
        headers={**TOKEN_HEADERS, "X-AdaOS-Device-Id": "device-phase45"},
    )
    assert header.status_code == 200
    settings = header.json()["settings"]
    assert settings["device_status"]["id"] == "device-phase45"
    assert settings["device_status"]["label"] == "Masha phone | device-phase45"
    assert settings["device_trust_status"] == "Masha phone | device-phase45"


def test_phase5_guest_invite_preview_claim_and_revoke_cuts_browser_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    object.__setattr__(get_ctx().settings, "root_token", "root-token")

    class _FakeRootResponse:
        def __enter__(self) -> "_FakeRootResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"ok":true,"claim_url":"https://inimatic.com/?mode=registration&user_code=DF0B-2729&zone=ru",'
                b'"invite":{"claim_url":"https://inimatic.com/?mode=registration&user_code=DF0B-2729&zone=ru"}}'
            )

    monkeypatch.setattr(personalization, "urlopen", lambda *args, **kwargs: _FakeRootResponse())
    client = _client()

    created = client.post(
        "/api/personalization/invites/guest",
        json={"scope": {"kind": "workspace", "id": "family"}, "expires_in_minutes": 10, "max_sessions": 2},
        headers=TOKEN_HEADERS,
    )
    assert created.status_code == 200
    invite = created.json()["invite"]
    invite_id = invite["invite_id"]
    assert invite["kind"] == "guest_join_link"
    assert invite["claim_url"] == "https://inimatic.com/?mode=registration&user_code=DF0B-2729&zone=ru"
    assert "adaos_invite=" not in invite["claim_url"]
    assert "target_subnet=" not in invite["claim_url"]
    assert "adaos_hub_base=" not in invite["claim_url"]
    assert "127.0.0.1" not in invite["claim_url"]

    preview = client.get(f"/api/personalization/invites/{invite_id}/preview")
    assert preview.status_code == 200
    assert preview.json()["preview"]["can_accept"] is True

    claim = client.post(
        f"/api/personalization/invites/{invite_id}/claim",
        json={"session_id": "browser-a", "device_id": "dev-browser-a", "device_name": "Guest browser"},
    )
    assert claim.status_code == 200
    assert claim.json()["session_id"] == "browser-a"
    assert claim.json()["device_id"] == "dev-browser-a"
    assert access_links.authorize_link("browser", "dev-browser-a") == (True, None)
    assert access_links.get_link("browser", "dev-browser-a")["admission_session_id"] == "browser-a"

    listed = client.get("/api/personalization/invites", headers=TOKEN_HEADERS)
    assert listed.status_code == 200
    assert any(item["invite_id"] == invite_id for item in listed.json()["invites"])

    revoked = client.post(
        f"/api/personalization/invites/{invite_id}/guest-sessions/revoke",
        json={"reason": "class ended"},
        headers=TOKEN_HEADERS,
    )
    assert revoked.status_code == 200
    assert revoked.json()["invite"]["status"] == "revoked"
    assert access_links.authorize_link("browser", "browser-a") == (False, "denied")
    assert access_links.authorize_link("browser", "dev-browser-a") == (False, "denied")


def test_phase5_targeted_invite_is_public_preview_and_single_use_claim() -> None:
    client = _client()

    created = client.post(
        "/api/personalization/invites/targeted",
        json={
            "profile_hint": "Masha",
            "subject_id": "masha",
            "role": "member",
            "scope": {"kind": "workspace", "id": "family"},
            "expires_in_minutes": 60,
        },
        headers=TOKEN_HEADERS,
    )
    assert created.status_code == 200
    invite_id = created.json()["invite"]["invite_id"]

    preview = client.get(f"/api/personalization/invites/{invite_id}/preview")
    assert preview.status_code == 200
    assert preview.json()["preview"]["profile_hint"] == "Masha"

    accepted = client.post(
        f"/api/personalization/invites/{invite_id}/claim",
        json={"subject_kind": "user", "subject_id": "masha", "session_id": "masha-pc"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["invite"]["status"] == "accepted"

    reused = client.post(
        f"/api/personalization/invites/{invite_id}/claim",
        json={"subject_kind": "user", "subject_id": "masha", "session_id": "masha-other"},
    )
    assert reused.status_code == 409
