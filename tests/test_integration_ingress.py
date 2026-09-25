from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adaos.domain.capability_binding_state import (
    BindingDefinition,
    CapabilityBindingStateContractError,
)
from adaos.domain.integration_ingress import (
    IngressProfile,
    RoutedIngressEnvelope,
)
from adaos.services.integrations.ingress import (
    GOOGLE_ISSUER_REF,
    GOOGLE_OAUTH_INGRESS_PROFILE_REF,
    LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
    LOCAL_GOOGLE_OAUTH_CALLBACK_URI,
    PUBLIC_CONNECTED_ENVIRONMENT_REF,
    PUBLIC_GOOGLE_OAUTH_CALLBACK_URI,
    IntegrationIngressBroker,
    IntegrationIngressError,
    google_oauth_ingress_profile,
    materialize_google_oauth_endpoint,
    public_google_oauth_callback_uri,
)
from adaos.apps.api import provider_oauth


class Vault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, *, default=None, scope="profile"):
        return self.values.get(key, default)

    def put(self, key: str, value: str, *, scope="profile", meta=None) -> None:
        self.values[key] = value

    def delete(self, key: str, *, scope="profile") -> None:
        self.values.pop(key, None)


def _begin(broker: IntegrationIngressBroker) -> dict:
    return broker.begin_authorization(
        provider_connection_ref="provider-connection:test",
        binding_instance_ref="binding-instance:test",
        application_ref="application:test",
        subject_ref="user:owner",
        return_intent="application.connection.refresh",
        correlation={"provider": "test", "release_digest": "sha256:" + "a" * 64},
    )


def test_ingress_profile_is_canonical_and_unknown_fields_fail_closed() -> None:
    profile = google_oauth_ingress_profile()

    assert profile.profile_ref == GOOGLE_OAUTH_INGRESS_PROFILE_REF
    assert profile.to_dict()["verification"]["pkce"] == "S256"
    invalid = {**profile.to_dict(), "callback_url": "https://attacker.test/callback"}
    with pytest.raises(CapabilityBindingStateContractError, match="Additional properties"):
        IngressProfile.from_mapping(invalid)


def test_portable_binding_declares_ingress_port_but_rejects_physical_route() -> None:
    binding = BindingDefinition.create(
        binding_definition_ref="binding-definition:test",
        version="1.0.0",
        capability_ref="capability:test",
        capability_version="1.0.0",
        entry_protocol="adaos.tool.v1",
        implementation_entrypoint="test.provider",
        state_support=(),
        modes=("production",),
        environment_constraints={"profile_classes": ["local"], "provider_features": []},
        authority_requirements=(),
        conformance_obligations=(),
        ingress_ports=(
            {
                "name": "authorization_return",
                "profile_ref": GOOGLE_OAUTH_INGRESS_PROFILE_REF,
                "required_guarantees": {"single_use": True, "local_acceptance": True},
            },
        ),
    )
    assert binding.to_dict()["ingress_ports"][0]["name"] == "authorization_return"

    with pytest.raises(CapabilityBindingStateContractError, match="physical ingress route"):
        BindingDefinition.create(
            binding_definition_ref="binding-definition:bad-route",
            version="1.0.0",
            capability_ref="capability:test",
            capability_version="1.0.0",
            entry_protocol="https://integrations.inimatic.com/v1/oauth/callback/cbp_bad_route",
            implementation_entrypoint="test.provider",
            state_support=(),
            modes=("production",),
            environment_constraints={"profile_classes": ["local"], "provider_features": []},
            authority_requirements=(),
            conformance_obligations=(),
        )


def test_loopback_attempt_survives_broker_restart_and_is_single_use() -> None:
    vault = Vault()
    broker = IntegrationIngressBroker(
        vault=vault,
        environment_profile_ref=LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
        clock=lambda: 2_000_000_000.0,
    )
    started = _begin(broker)

    assert started["redirect_uri"] == LOCAL_GOOGLE_OAUTH_CALLBACK_URI
    assert started["attempt"]["state_hash"] != started["state"]
    assert started["state"] not in json.dumps(started["attempt"])

    restarted = IntegrationIngressBroker(
        vault=vault,
        environment_profile_ref=LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
        clock=lambda: 2_000_000_001.0,
    )
    consumed = restarted.consume_authorization(
        state=started["state"],
        expected_profile_ref=GOOGLE_OAUTH_INGRESS_PROFILE_REF,
        expected_issuer_ref=GOOGLE_ISSUER_REF,
        expected_attempt_ref=started["attempt"]["attempt_ref"],
    )
    assert consumed["correlation"]["provider"] == "test"
    assert consumed["code_verifier"] == started["code_verifier"]
    with pytest.raises(IntegrationIngressError, match="oauth_state_replayed"):
        restarted.consume_authorization(
            state=started["state"],
            expected_profile_ref=GOOGLE_OAUTH_INGRESS_PROFILE_REF,
            expected_issuer_ref=GOOGLE_ISSUER_REF,
        )


def test_attempt_expiry_and_wrong_issuer_fail_closed() -> None:
    now = [2_000_000_000.0]
    broker = IntegrationIngressBroker(vault=Vault(), clock=lambda: now[0])
    expired = _begin(broker)
    now[0] += 601
    with pytest.raises(IntegrationIngressError, match="oauth_state_expired"):
        broker.consume_authorization(
            state=expired["state"],
            expected_profile_ref=GOOGLE_OAUTH_INGRESS_PROFILE_REF,
            expected_issuer_ref=GOOGLE_ISSUER_REF,
        )

    current = _begin(broker)
    with pytest.raises(IntegrationIngressError, match="oauth_issuer_mismatch"):
        broker.consume_authorization(
            state=current["state"],
            expected_profile_ref=GOOGLE_OAUTH_INGRESS_PROFILE_REF,
            expected_issuer_ref="issuer:other",
        )


def test_public_attempt_registers_only_hashed_rendezvous_and_decrypts_envelope() -> None:
    captured: list[dict] = []

    class Registrar:
        def register_attempt(self, projection):
            captured.append(dict(projection))
            return {"ok": True}

    vault = Vault()
    now = 2_000_000_000.0
    broker = IntegrationIngressBroker(
        vault=vault,
        environment_profile_ref=PUBLIC_CONNECTED_ENVIRONMENT_REF,
        clock=lambda: now,
        public_registrar=Registrar(),
    )
    started = _begin(broker)

    assert started["redirect_uri"] == PUBLIC_GOOGLE_OAUTH_CALLBACK_URI
    assert len(captured) == 1
    projection = captured[0]
    assert projection["zone_id"] == "us"
    assert projection["state_hash"] == started["state_hash"]
    assert started["state"] not in json.dumps(projection)

    public_key = serialization.load_pem_public_key(
        projection["delivery_public_key_pem"].encode("ascii")
    )
    content_key = AESGCM.generate_key(bit_length=256)
    nonce = b"0123456789ab"
    payload = json.dumps(
        {
            "attempt_ref": projection["attempt_ref"],
            "profile_ref": projection["profile_ref"],
            "state": started["state"],
            "code": "authorization-code",
            "error": "",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    aad = (
        f"{projection['attempt_ref']}\0{projection['zone_id']}\0"
        f"{projection['route_binding_ref']}\0{projection['generation']}"
    ).encode("utf-8")
    encrypted = AESGCM(content_key).encrypt(nonce, payload, aad)
    encrypted_key = public_key.encrypt(
        content_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    envelope = RoutedIngressEnvelope.create(
        envelope_ref="ingress-envelope:test",
        attempt_ref=projection["attempt_ref"],
        endpoint_ref=projection["endpoint_ref"],
        endpoint_revision=projection["endpoint_revision"],
        profile_ref=projection["profile_ref"],
        issuer_ref=projection["issuer_ref"],
        zone_id=projection["zone_id"],
        audience=projection["route_binding_ref"],
        route_binding_ref=projection["route_binding_ref"],
        generation=projection["generation"],
        issued_at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        expires_at=datetime.fromtimestamp(now + 120, tz=timezone.utc).isoformat(),
        algorithm="RSA-OAEP-256+A256GCM",
        encrypted_key=base64.urlsafe_b64encode(encrypted_key).decode().rstrip("="),
        nonce=base64.urlsafe_b64encode(nonce).decode().rstrip("="),
        ciphertext=base64.urlsafe_b64encode(encrypted[:-16]).decode().rstrip("="),
        auth_tag=base64.urlsafe_b64encode(encrypted[-16:]).decode().rstrip("="),
    )
    decrypted = broker.decrypt_routed_envelope(envelope.to_dict())
    assert decrypted["state"] == started["state"]
    assert decrypted["code"] == "authorization-code"

    invalid_cases = (
        ("profile_ref", "ingress-profile:oauth.other@1", "profile_mismatch"),
        ("issuer_ref", "issuer:other", "issuer_mismatch"),
        ("zone_id", "ru", "zone_mismatch"),
        ("endpoint_ref", "ingress-endpoint:other", "endpoint_mismatch"),
        ("endpoint_revision", 2, "generation_mismatch"),
        ("audience", "ingress-route:other", "audience_mismatch"),
        ("route_binding_ref", "ingress-route:other", "route_mismatch"),
        ("generation", 2, "generation_mismatch"),
        (
            "expires_at",
            datetime.fromtimestamp(now - 1, tz=timezone.utc).isoformat(),
            "envelope_expired",
        ),
    )
    unsigned = envelope.to_dict()
    unsigned.pop("envelope_digest")
    for field, replacement, error in invalid_cases:
        changed = RoutedIngressEnvelope.create(**{**unsigned, field: replacement})
        with pytest.raises(IntegrationIngressError, match=error):
            broker.decrypt_routed_envelope(changed.to_dict())


def test_public_endpoint_materialization_is_zone_aware_and_portable_profile_is_not() -> None:
    central = materialize_google_oauth_endpoint(
        PUBLIC_CONNECTED_ENVIRONMENT_REF,
        zone_id="eu",
    ).to_dict()
    ru = materialize_google_oauth_endpoint(
        PUBLIC_CONNECTED_ENVIRONMENT_REF,
        zone_id="ru",
    ).to_dict()

    assert central["callback_uri"] == PUBLIC_GOOGLE_OAUTH_CALLBACK_URI
    assert central["zone_id"] == "eu"
    assert central["endpoint_ref"] == "ingress-endpoint:google-oauth-public-eu"
    assert ru["callback_uri"] == public_google_oauth_callback_uri("ru")
    assert ru["callback_uri"].startswith("https://ru.integrations.inimatic.com/")
    assert ru["zone_id"] == "ru"
    assert ru["route_binding_ref"] == "ingress-route:google-oauth-public-ru"
    assert central["profile_ref"] == ru["profile_ref"]
    assert central["profile_digest"] == ru["profile_digest"]


def test_public_broker_registers_the_selected_zone() -> None:
    captured: list[dict] = []

    class Registrar:
        def register_attempt(self, projection):
            captured.append(dict(projection))
            return {"ok": True}

    broker = IntegrationIngressBroker(
        vault=Vault(),
        environment_profile_ref=PUBLIC_CONNECTED_ENVIRONMENT_REF,
        zone_id="ru",
        clock=lambda: 2_000_000_000.0,
        public_registrar=Registrar(),
    )
    started = _begin(broker)

    assert started["redirect_uri"] == public_google_oauth_callback_uri("ru")
    assert started["attempt"]["zone_id"] == "ru"
    assert captured[0]["zone_id"] == "ru"


def test_public_delivery_returns_digest_addressed_exact_acknowledgement(monkeypatch) -> None:
    envelope = {
        "envelope_ref": "ingress-envelope:test",
        "attempt_ref": "callback-attempt:test",
        "endpoint_ref": "ingress-endpoint:google-oauth-public-us",
        "audience": "ingress-route:google-oauth-public-us",
    }

    class Broker:
        def decrypt_routed_envelope(self, _envelope):
            return {
                "profile_ref": GOOGLE_OAUTH_INGRESS_PROFILE_REF,
                "attempt_ref": "callback-attempt:test",
                "state": "state",
                "code": "code",
                "error": "",
            }

    class Provider:
        def complete_authorization(self, **_values):
            return {"email_address": "owner@example.test"}

    monkeypatch.setattr(provider_oauth, "broker_from_context", lambda _ctx: Broker())
    monkeypatch.setattr(
        provider_oauth.GoogleGmailProvider,
        "from_context",
        classmethod(lambda _cls, _ctx: Provider()),
    )

    response = asyncio.run(
        provider_oauth.deliver_oauth_ingress(
            envelope=envelope,
            ctx=SimpleNamespace(),
        )
    )
    acknowledgement = json.loads(response.body)

    assert response.status_code == 200
    assert acknowledgement["schema"] == "adaos.integration.ingress_acknowledgement.v1"
    assert acknowledgement["envelope_ref"] == envelope["envelope_ref"]
    assert acknowledgement["attempt_ref"] == envelope["attempt_ref"]
    assert acknowledgement["status"] == "accepted"
    assert acknowledgement["ack_digest"].startswith("sha256:")

