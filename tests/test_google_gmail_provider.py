from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.application_access import ApplicationPermissionProfile
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectCompositionLock,
    ProjectMemberLock,
    ProjectRelease,
    canonical_payload_digest,
)
from adaos.services.applications import ApplicationService, ApplicationStore
from adaos.services.providers.google_gmail import (
    GMAIL_API_ORIGIN,
    GMAIL_MODIFY_SCOPE,
    GOOGLE_TOKEN_ENDPOINT,
    GoogleGmailProvider,
    GoogleGmailProviderError,
)
from adaos.sdk.builder.applications import _with_release_setup_contract
from adaos.sdk.providers import gmail as gmail_sdk
from adaos.apps.api import provider_oauth
from adaos.domain.personalization_access import SubjectRef


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


class FakeVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, *, default=None, scope="profile"):
        return self.values.get(key, default)

    def put(self, key: str, value: str, *, scope="profile", meta=None) -> None:
        self.values[key] = value

    def delete(self, key: str, *, scope="profile") -> None:
        self.values.pop(key, None)


class CountingVault(FakeVault):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def get(self, key: str, *, default=None, scope="profile"):
        self.reads.append(key)
        return super().get(key, default=default, scope=scope)


@dataclass
class FakeResponse:
    status_code: int
    payload: dict

    @property
    def content(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def json(self):
        return self.payload


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected transport request")
        return self.responses.pop(0)


def _application() -> Application:
    return Application(
        application_id="gmail_mail_client",
        legacy_project_id="gmail_mail_client",
        publisher_ref="subnet:home",
        slug="gmail_mail_client",
        display={"title": "Gmail Mail Client", "summary": "Mail"},
        visibility="private",
        entrypoints=(
            {
                "entrypoint_id": "main",
                "presentation_ref": "scenario:gmail_mail_client",
            },
        ),
        publisher={
            "publisher_ref": "subnet:home",
            "display_name": "Home",
            "subnet_short_ref": "home",
            "release_key_ref": "subnet-key:release-signing:1",
            "release_key_fingerprint": DIGEST_C,
            "home_zone": "local-dev",
            "trust_relation": "local",
        },
    )


def _release(service: ApplicationService) -> ApplicationRelease:
    profile = ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": [
                {
                    "id": "providers.google.gmail",
                    "purpose": "Use the connected Gmail account.",
                    "approval_policy": "explicit",
                }
            ],
            "optional": [],
            "external_providers": [
                {
                    "id": "google.gmail",
                    "account_id": "google.gmail",
                    "title": "Gmail account",
                    "purpose": "Read and manage mail requested by the user.",
                    "required": True,
                    "destination": "gmail.googleapis.com",
                    "account_modes": ["delegated_user"],
                    "scopes": [GMAIL_MODIFY_SCOPE],
                }
            ],
        }
    )
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/gmail-mail-client",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("projects/gmail_mail_client/",),
    )
    package = ArtifactPackageRef(
        kind="skill",
        artifact_id="gmail_mail_client_provider_skill",
        version="1.0.0",
        digest=DIGEST_A,
        manifest_digest=DIGEST_B,
        source_ref=source,
    )
    composition = ProjectCompositionLock(
        project_definition_digest=canonical_payload_digest(
            {"id": "gmail_mail_client", "profile": profile.to_dict()}
        ),
        profiles=("adaos.application.v1",),
        members=(
            ProjectMemberLock(
                ref=package.key,
                package_digest=package.digest,
                role="primary",
                exposure="project_only",
                lifecycle="bound",
                relations=("realizes",),
            ),
        ),
        project_dependencies=(),
        entrypoints=(),
        compatibility={},
        lifecycle={},
        permission_profile=profile.to_dict(),
        application_roles=(),
    )
    project = ProjectRelease(
        project_id="gmail_mail_client",
        version="1.0.0",
        source_ref=source,
        components=(package,),
        permissions=profile.flat_permissions,
        validation_evidence=({"status": "passed", "suite": "provider"},),
        composition_lock=composition,
    ).seal()
    return service.register_release(
        ApplicationRelease(
            application_id="gmail_mail_client",
            publisher_ref="subnet:home",
            project_release=project,
            accepted_candidate_id="candidate.gmail.1",
            acceptance_evidence=({"status": "accepted"},),
            provenance_refs=(DIGEST_C,),
            lifecycle="trial",
        )
    )


def _provider(
    tmp_path: Path,
    responses: list[FakeResponse],
    *,
    clock=lambda: 2_000_000_000.0,
):
    applications = ApplicationService(ApplicationStore(tmp_path / "applications"))
    applications.register(_application())
    release = _release(applications)
    vault = FakeVault()
    transport = FakeTransport(responses)
    provider = GoogleGmailProvider(
        vault=vault,
        applications=applications,
        transport=transport,
        client_id="google-client-id",
        client_secret="google-client-secret",
        redirect_uri="http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback",
        clock=clock,
    )
    return provider, vault, transport, release


def _connect(tmp_path: Path):
    provider, vault, transport, release = _provider(
        tmp_path,
        [
            FakeResponse(
                200,
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "scope": GMAIL_MODIFY_SCOPE,
                    "expires_in": 3600,
                },
            ),
            FakeResponse(200, {"emailAddress": "owner@example.test"}),
        ],
    )
    start = provider.begin_authorization(
        application_id="gmail_mail_client",
        release_digest=release.release_digest,
        subject_ref="user:owner",
    )
    query = parse_qs(urlparse(start["authorization_url"]).query)
    result = provider.complete_authorization(
        state=query["state"][0], code="authorization-code"
    )
    return provider, vault, transport, release, result


def test_oauth_connection_uses_state_pkce_and_keeps_tokens_out_of_application_state(
    tmp_path: Path,
) -> None:
    provider, vault, transport, release, result = _connect(tmp_path)

    assert result == {
        "ok": True,
        "provider_id": "google.gmail",
        "account_id": "google.gmail",
        "status": "connected",
        "email_address": "owner@example.test",
        "scopes": [GMAIL_MODIFY_SCOPE],
    }
    auth_call, profile_call = transport.calls
    assert auth_call[0:2] == ("POST", GOOGLE_TOKEN_ENDPOINT)
    assert auth_call[2]["data"]["code_verifier"]
    assert auth_call[2]["data"]["client_secret"] == "google-client-secret"
    assert profile_call[1] == f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/profile"

    accounts = provider.access.connected_accounts("gmail_mail_client")
    serialized_accounts = json.dumps(accounts)
    assert accounts[0]["status"] == "connected"
    assert accounts[0]["scopes"] == [GMAIL_MODIFY_SCOPE]
    assert "access-secret" not in serialized_accounts
    assert "refresh-secret" not in serialized_accounts
    assert any("access-secret" in value for value in vault.values.values())
    assert not any("oauth-state" in key for key in vault.values)


def test_dev_preview_uses_pinned_verified_provider_declaration_without_release(
    tmp_path: Path,
) -> None:
    applications = ApplicationService(ApplicationStore(tmp_path / "applications"))
    applications.register(_application())
    vault = FakeVault()
    transport = FakeTransport(
        [
            FakeResponse(
                200,
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "scope": GMAIL_MODIFY_SCOPE,
                    "expires_in": 3600,
                },
            ),
            FakeResponse(200, {"emailAddress": "owner@example.test"}),
        ]
    )
    provider = GoogleGmailProvider(
        vault=vault,
        applications=applications,
        transport=transport,
        client_id="google-client-id",
        client_secret="google-client-secret",
        redirect_uri="http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback",
        clock=lambda: 2_000_000_000.0,
    )
    profile = {
        "schema": "adaos.application.permission_profile.v1",
        "required": [
            {
                "id": "providers.google.gmail",
                "purpose": "Use Gmail.",
            }
        ],
        "optional": [],
        "external_providers": [
            {
                "id": "google.gmail",
                "account_id": "google.gmail",
                "purpose": "Read mail requested by the user.",
                "required": True,
                "destination": "gmail.googleapis.com",
                "account_modes": ["delegated_user"],
                "scopes": [GMAIL_MODIFY_SCOPE],
            }
        ],
    }

    start = provider.begin_authorization(
        application_id="gmail_mail_client",
        release_digest=DIGEST_A,
        subject_ref="user:owner",
        candidate_permission_profile=profile,
    )
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    pending = json.loads(vault.values[provider._state_key(state)])
    assert pending["candidate_permission_profile"] == profile

    result = provider.complete_authorization(state=state, code="authorization-code")

    assert result["status"] == "connected"
    account = provider.access.connected_accounts("gmail_mail_client")[0]
    assert account["status"] == "connected"
    assert account["provider_id"] == "google.gmail"


def test_oauth_state_is_one_use_and_denial_never_creates_a_credential(
    tmp_path: Path,
) -> None:
    provider, vault, _transport, release = _provider(tmp_path, [])
    start = provider.begin_authorization(
        application_id="gmail_mail_client",
        release_digest=release.release_digest,
        subject_ref="user:owner",
    )
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]

    with pytest.raises(GoogleGmailProviderError, match="oauth_authorization_denied"):
        provider.complete_authorization(state=state, error="access_denied")
    with pytest.raises(GoogleGmailProviderError, match="oauth_state_invalid"):
        provider.complete_authorization(state=state, code="replay")

    assert not any("credential.v1" in value for value in vault.values.values())
    assert provider.access.connected_accounts("gmail_mail_client")[0]["status"] == "denied"


def test_provider_exposes_only_fixed_gmail_operations_and_never_retries_a_send(
    tmp_path: Path,
) -> None:
    provider, _vault, transport, release, _result = _connect(tmp_path)
    transport.calls.clear()
    transport.responses.append(FakeResponse(200, {"id": "sent-1", "threadId": "t-1"}))

    sent = provider.execute(
        "send_message",
        application_id="gmail_mail_client",
        release_digest=release.release_digest,
        subject_ref="user:owner",
        account_id="google.gmail",
        arguments={"raw": "RnJvbTogbWVAZXhhbXBsZS50ZXN0", "idempotency_key": "send-1"},
    )

    assert sent["result"] == {"id": "sent-1", "threadId": "t-1"}
    assert transport.calls == [
        (
            "POST",
            f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages/send",
            transport.calls[0][2],
        )
    ]
    assert transport.calls[0][2]["headers"]["Authorization"] == "Bearer access-secret"
    assert "access-secret" not in json.dumps(sent)

    with pytest.raises(GoogleGmailProviderError, match="gmail_operation_not_supported"):
        provider.execute(
            "arbitrary_http",
            application_id="gmail_mail_client",
            release_digest=release.release_digest,
            subject_ref="user:owner",
            arguments={"url": "https://attacker.invalid"},
        )
    assert len(transport.calls) == 1

    with pytest.raises(GoogleGmailProviderError, match="gmail_message_size_invalid"):
        provider.execute(
            "send_message",
            application_id="gmail_mail_client",
            release_digest=release.release_digest,
            subject_ref="user:owner",
            arguments={"raw": "not base64!", "idempotency_key": "send-2"},
        )
    assert len(transport.calls) == 1


def test_sdk_accepts_trusted_user_subject_for_a_session_caller(monkeypatch) -> None:
    provider = object()
    application = {
        "application_id": "gmail_mail_client",
        "release_digest": DIGEST_A,
        "subject_ref": "user:owner",
    }
    monkeypatch.setattr(gmail_sdk, "require_ctx", lambda _operation: object())
    monkeypatch.setattr(
        gmail_sdk, "require_skill_capability", lambda _ctx, _capability: None
    )
    monkeypatch.setattr(gmail_sdk, "current_application", lambda: application)
    monkeypatch.setattr(
        gmail_sdk, "current_caller", lambda: SubjectRef("session", "browser-session")
    )
    monkeypatch.setattr(
        gmail_sdk.GoogleGmailProvider,
        "from_context",
        classmethod(lambda _cls, _ctx: provider),
    )

    assert gmail_sdk._invocation() == (provider, application, "user:owner")


def test_sdk_exports_the_bounded_public_provider_error() -> None:
    error = gmail_sdk.GmailProviderError(
        "gmail_provider_unavailable", status_code=503, retryable=True
    )

    assert error.to_dict() == {
        "ok": False,
        "error": "gmail_provider_unavailable",
        "status_code": 503,
        "retryable": True,
    }


def test_provider_context_defers_oauth_keyring_reads_until_authorization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    vault = CountingVault()
    vault.values.update({
        "provider:google.oauth:client_id": "client-id",
        "provider:google.oauth:client_secret": "client-secret",
    })
    monkeypatch.delenv("ADAOS_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("ADAOS_GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    provider = GoogleGmailProvider.from_context(
        SimpleNamespace(
            credential_vault=vault,
            authority_state_dir=tmp_path,
            paths=SimpleNamespace(state_dir=lambda: tmp_path),
            config=SimpleNamespace(local_api_url="http://127.0.0.1:8777"),
        )
    )

    assert vault.reads == []
    provider._ensure_oauth_configuration()
    assert vault.reads == [
        "provider:google.oauth:client_id",
        "provider:google.oauth:client_secret",
    ]
    assert provider.client_id == "client-id"
    assert provider.client_secret == "client-secret"


def test_oauth_callback_returns_safe_error_when_provider_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        provider_oauth.GoogleGmailProvider,
        "from_context",
        classmethod(
            lambda _cls, _ctx: (_ for _ in ()).throw(
                GoogleGmailProviderError("credential_vault_unavailable")
            )
        ),
    )

    response = asyncio.run(
        provider_oauth.google_gmail_oauth_callback(ctx=SimpleNamespace())
    )

    assert response.status_code == 400
    assert b"credential vault unavailable" in response.body


def test_provider_refreshes_expired_token_and_updates_redacted_account_generation(
    tmp_path: Path,
) -> None:
    now = [2_000_000_000.0]
    provider, vault, transport, release = _provider(
        tmp_path,
        [
            FakeResponse(
                200,
                {
                    "access_token": "old-access",
                    "refresh_token": "refresh-secret",
                    "scope": GMAIL_MODIFY_SCOPE,
                    "expires_in": 1,
                },
            ),
            FakeResponse(200, {"emailAddress": "owner@example.test"}),
        ],
        clock=lambda: now[0],
    )
    start = provider.begin_authorization(
        application_id="gmail_mail_client",
        release_digest=release.release_digest,
        subject_ref="user:owner",
    )
    provider.complete_authorization(
        state=parse_qs(urlparse(start["authorization_url"]).query)["state"][0],
        code="code",
    )
    now[0] = 2_000_000_100.0
    transport.calls.clear()
    transport.responses.extend(
        [
            FakeResponse(200, {"access_token": "new-access", "expires_in": 3600}),
            FakeResponse(200, {"messages": []}),
        ]
    )

    result = provider.execute(
        "list_messages",
        application_id="gmail_mail_client",
        release_digest=release.release_digest,
        subject_ref="user:owner",
        arguments={"max_results": 25},
    )

    assert result["result"] == {"messages": []}
    assert [call[1] for call in transport.calls] == [
        GOOGLE_TOKEN_ENDPOINT,
        f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages",
    ]
    assert transport.calls[1][2]["headers"]["Authorization"] == "Bearer new-access"
    account = provider.access.connected_accounts("gmail_mail_client")[0]
    assert account["revision"] == 2
    assert "new-access" not in json.dumps(account)
    assert any("new-access" in value for value in vault.values.values())


def test_trial_release_compiles_connected_account_setup_from_immutable_declarations(
    tmp_path: Path,
) -> None:
    applications = ApplicationService(ApplicationStore(tmp_path / "applications"))
    applications.register(_application())
    envelope = _release(applications)
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        "\n".join(
            [
                "name: gmail_mail_client_provider_skill",
                "version: 1.0.0",
                "configuration:",
                "  credentials: {}",
            ]
        ),
        encoding="utf-8",
    )

    class Runtime:
        packages = envelope.project_release.components

        @staticmethod
        def verified_source(_package):
            return skill_root

    compiled = _with_release_setup_contract(envelope, Runtime())

    assert compiled.setup_contract is not None
    assert compiled.setup_contract.payload["connected_accounts"] == [
        {
            "id": "google.gmail",
            "title": "Gmail account",
            "purpose": "Read and manage mail requested by the user.",
            "required": True,
            "scopes": [GMAIL_MODIFY_SCOPE],
        }
    ]
    assert compiled.setup_contract.payload["permissions"][0]["id"] == (
        "providers.google.gmail"
    )
