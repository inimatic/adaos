from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api.application_report_relay import router
from adaos.services.applications import register_development_report_service
from adaos.services.applications.runtime import create_local_development_report_service
from adaos.services.applications.report_crypto import DevelopmentReportEnvelopeCrypto
from adaos.services.applications.report_directory import (
    SubnetKeyDirectoryAuthority,
    SubnetKeyDirectoryClient,
)
from adaos.services.applications.report_keys import SubnetPurposeKeyStore
from adaos.services.applications.report_relay import (
    DurableDevelopmentReportRelay,
    HttpDevelopmentReportRelayPeer,
    RootMailboxDevelopmentReportRelay,
)
from adaos.domain.development_report import DevelopmentReportAck
from adaos.services.root.client import RootHttpClient


def _relays(tmp_path):
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    stores = {
        subnet: SubnetPurposeKeyStore(tmp_path / subnet.replace(":", "_"))
        for subnet in ("subnet:guest", "subnet:publisher")
    }
    authority = SubnetKeyDirectoryAuthority(tmp_path / "directory", zone_id="directory")
    projection = None
    for subnet, zone in (
        ("subnet:guest", "zone_a"),
        ("subnet:publisher", "zone_b"),
    ):
        stores[subnet].ensure_key(subnet, "message_signing")
        stores[subnet].ensure_key(subnet, "message_encryption")
        projection = authority.publish_subnet(
            subnet,
            home_zone=zone,
            keys=stores[subnet].list_public(subnet),
        )
    directory = SubnetKeyDirectoryClient()
    directory.update(projection)
    source = DurableDevelopmentReportRelay(
        tmp_path / "source",
        zone_id="zone_a",
        directory=directory,
        now=lambda: now,
    )
    destination = DurableDevelopmentReportRelay(
        tmp_path / "destination",
        zone_id="zone_b",
        directory=directory,
        now=lambda: now,
    )
    source.trust_peer(destination.public_identity())
    destination.trust_peer(source.public_identity())
    envelope = DevelopmentReportEnvelopeCrypto(
        key_store=stores["subnet:guest"],
        directory=directory,
        now=lambda: now,
    ).seal(
        {"report": "encrypted"},
        message_kind="report",
        sender_subnet_ref="subnet:guest",
        recipient_subnet_ref="subnet:publisher",
        message_id="msg.http-forward",
    )
    return directory, source, destination, envelope


def test_live_root_http_peer_preserves_durable_signed_handoff(
    tmp_path,
    monkeypatch,
) -> None:
    directory, source, destination, envelope = _relays(tmp_path)
    app = FastAPI()
    app.include_router(router)
    http = TestClient(app)
    monkeypatch.setenv("ADAOS_ROOT_RELAY_INGRESS_TOKEN", "relay-secret")
    register_development_report_service(
        SimpleNamespace(relay=destination, directory=directory)
    )

    class Client:
        def accept_development_report_forward(self, **kwargs):
            token = kwargs.pop("relay_token")
            response = http.post(
                "/v1/root/applications/development-reports/relay/forward",
                headers={"X-AdaOS-Relay-Token": token},
                json=kwargs,
            )
            assert response.status_code == 200
            return response.json()

    try:
        assert http.get(
            "/v1/root/applications/development-reports/relay/identity"
        ).status_code == 401
        assert http.post(
            "/v1/root/applications/development-reports/relay/forward",
            headers={
                "X-AdaOS-Relay-Token": "relay-secret",
                "Content-Length": "4000001",
            },
            content=b"{}",
        ).status_code == 413
        identity = http.get(
            "/v1/root/applications/development-reports/relay/identity",
            headers={"X-AdaOS-Relay-Token": "relay-secret"},
        ).json()["identity"]
        signed_directory = http.get(
            "/v1/root/applications/development-reports/relay/directory",
            headers={"X-AdaOS-Relay-Token": "relay-secret"},
        ).json()["directory"]
        verifying_directory = SubnetKeyDirectoryClient(
            pinned_root_key_id=signed_directory["root_key_id"]
        )
        verifying_directory.update(signed_directory)
        assert verifying_directory.home_zone("subnet:publisher") == "zone_b"

        peer = HttpDevelopmentReportRelayPeer(
            Client(),
            identity=identity,
            relay_token="relay-secret",
        )
        source.enqueue(envelope)
        receipt = source.forward(envelope.message_id, peer)

        assert receipt["disposition"] == "forwarded"
        assert source.forward(envelope.message_id, peer) == receipt
        delivered = destination.poll("subnet:publisher")
        assert [item["envelope"]["message_id"] for item in delivered] == [
            envelope.message_id
        ]
    finally:
        register_development_report_service(None)


def test_root_http_client_uses_bounded_report_relay_routes() -> None:
    class Client(RootHttpClient):
        def __init__(self) -> None:
            super().__init__(base_url="https://zone-b.example", verify=True)
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def _request(self, method: str, path: str, **kwargs: Any) -> Any:
            self.calls.append((method, path, kwargs))
            return {"ok": True, "receipt": {}}

    client = Client()
    client.get_development_report_relay_identity(relay_token="token")
    client.get_development_report_directory(relay_token="token")
    client.accept_development_report_forward(
        envelope={"message_id": "msg.one"},
        offer={"message_id": "msg.one"},
        source_identity={"zone_id": "zone_a"},
        relay_token="token",
    )
    client.flush_development_report_relay(relay_token="token")

    assert [(method, path) for method, path, _ in client.calls] == [
        ("GET", "/v1/root/applications/development-reports/relay/identity"),
        ("GET", "/v1/root/applications/development-reports/relay/directory"),
        ("POST", "/v1/root/applications/development-reports/relay/forward"),
        ("POST", "/v1/root/applications/development-reports/relay/flush"),
    ]
    assert all(
        call[2]["headers"] == {"X-AdaOS-Relay-Token": "token"}
        for call in client.calls
    )
    assert client.calls[2][2]["json"]["source_identity"] == {"zone_id": "zone_a"}


def test_root_http_client_uses_authenticated_mailbox_routes() -> None:
    class Client(RootHttpClient):
        def __init__(self) -> None:
            super().__init__(base_url="https://zone.example", verify=True)
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def _request(self, method: str, path: str, **kwargs: Any) -> Any:
            self.calls.append((method, path, kwargs))
            if method == "GET" and path.endswith("/directory"):
                return {"ok": True, "directory": {}}
            if method == "GET":
                return {"ok": True, "deliveries": []}
            if path.endswith("/ack"):
                return {"ok": True, "receipt": {}}
            return {"ok": True, "accepted": True}

    client = Client()
    client.publish_development_report_directory_entry(
        subnet_ref="subnet:one",
        home_zone="zone_a",
        display_name="One",
        keys=[],
    )
    client.get_shared_development_report_directory()
    client.enqueue_development_report_message(envelope={"message_id": "msg.one"})
    client.poll_development_report_messages(limit=7)
    client.acknowledge_development_report_message(
        message_id="msg.one",
        delivery_id="delivery.one",
        disposition="accepted",
    )

    assert [(method, path) for method, path, _ in client.calls] == [
        ("PUT", "/v1/hub/development-reports/directory"),
        ("GET", "/v1/hub/development-reports/directory"),
        ("POST", "/v1/hub/development-reports/messages"),
        ("GET", "/v1/hub/development-reports/messages"),
        ("POST", "/v1/hub/development-reports/messages/msg.one/ack"),
    ]
    assert client.calls[3][2]["params"] == {"limit": 7}


def test_http_forward_retry_reuses_durable_offer_after_lost_response(tmp_path) -> None:
    _directory, source, destination, envelope = _relays(tmp_path)

    class UncertainClient:
        def __init__(self) -> None:
            self.offers = []

        def accept_development_report_forward(self, **kwargs):
            self.offers.append(dict(kwargs["offer"]))
            receipt = destination.accept_forward(
                kwargs["envelope"],
                offer=kwargs["offer"],
                source_identity=kwargs["source_identity"],
            )
            if len(self.offers) == 1:
                raise OSError("response lost")
            return {"ok": True, "receipt": receipt}

    client = UncertainClient()
    peer = HttpDevelopmentReportRelayPeer(
        client,
        identity=destination.public_identity(),
        relay_token="relay-secret",
    )
    source.enqueue(envelope)

    try:
        source.forward(envelope.message_id, peer)
    except OSError as exc:
        assert str(exc) == "response lost"
    else:
        raise AssertionError("lost response must remain uncertain")
    receipt = source.forward(envelope.message_id, peer)

    assert client.offers[0] == client.offers[1]
    assert receipt["disposition"] == "forwarded"
    assert len(destination.poll("subnet:publisher")) == 1


def test_runtime_loads_pinned_http_peers_without_network_bootstrap(
    tmp_path,
    monkeypatch,
) -> None:
    _directory, _source, destination, _envelope = _relays(tmp_path / "fixtures")
    config = {
        "zone_b": {
            "base_url": "https://zone-b.example",
            "relay_token": "relay-secret",
            "identity": destination.public_identity(),
        }
    }
    monkeypatch.setenv("ADAOS_DEVELOPMENT_REPORT_ROOT_PEERS_JSON", json.dumps(config))

    service = create_local_development_report_service(
        tmp_path / "runtime",
        subnet_ref="subnet:local",
        zone_id="zone_a",
    )

    assert set(service.relay_peers) == {"zone_b"}
    assert service.relay_peers["zone_b"].public_identity() == destination.public_identity()


def test_root_mailbox_transport_keeps_ciphertext_until_recipient_ack(tmp_path) -> None:
    directory, _source, _destination, envelope = _relays(tmp_path)

    class MailboxClient:
        def __init__(self) -> None:
            self.messages: dict[str, dict[str, Any]] = {}

        def enqueue_development_report_message(self, *, envelope):
            message_id = envelope["message_id"]
            duplicate = message_id in self.messages
            self.messages.setdefault(message_id, dict(envelope))
            return {"accepted": True, "duplicate": duplicate, "message_id": message_id}

        def poll_development_report_messages(self, *, limit):
            return {
                "deliveries": [
                    {
                        "delivery_id": f"delivery.{message_id}",
                        "attempt": 1,
                        "envelope": value,
                    }
                    for message_id, value in list(self.messages.items())[:limit]
                ]
            }

        def acknowledge_development_report_message(
            self, *, message_id, delivery_id, disposition
        ):
            self.messages.pop(message_id)
            return {
                "receipt": {
                    "message_id": message_id,
                    "delivery_id": delivery_id,
                    "disposition": disposition,
                }
            }

    mailbox = MailboxClient()

    def resolver(_zone):
        return mailbox

    sender = RootMailboxDevelopmentReportRelay(
        zone_id="zone_a", directory=directory, client_for_zone=resolver
    )
    recipient = RootMailboxDevelopmentReportRelay(
        zone_id="zone_b", directory=directory, client_for_zone=resolver
    )

    assert sender.enqueue(envelope)["duplicate"] is False
    assert sender.enqueue(envelope)["duplicate"] is True
    delivery = recipient.poll("subnet:publisher", limit=1)[0]
    assert delivery["envelope"]["ciphertext_b64"] == envelope.ciphertext_b64
    assert envelope.message_id in mailbox.messages

    receipt = recipient.acknowledge(
        DevelopmentReportAck(
            message_id=envelope.message_id,
            recipient_subnet_ref="subnet:publisher",
            disposition="accepted",
            delivery_id=delivery["delivery_id"],
            accepted_at="2026-09-05T00:00:00+00:00",
        )
    )
    assert receipt["disposition"] == "accepted"
    assert mailbox.messages == {}
