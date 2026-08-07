from adaos.services.registry.subnet_member_availability import (
    subnet_member_availability_scope,
)


def test_subnet_member_availability_keeps_connected_and_online_members_active(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_MEMBER_AVAILABILITY_DORMANT_AFTER_S", "600")

    connected = subnet_member_availability_scope(
        connected=True,
        online=False,
        last_seen_at=1.0,
        now=10_000.0,
    )
    online = subnet_member_availability_scope(
        connected=False,
        online=True,
        last_seen_at=1.0,
        now=10_000.0,
    )

    assert connected["scope"] == "active"
    assert connected["reason"] == "member_link_connected"
    assert online["scope"] == "active"
    assert online["reason"] == "directory_online"


def test_subnet_member_availability_separates_recent_offline_from_dormant(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_MEMBER_AVAILABILITY_DORMANT_AFTER_S", "600")

    recent = subnet_member_availability_scope(
        connected=False,
        online=False,
        last_seen_at=9_500.0,
        now=10_000.0,
    )
    dormant = subnet_member_availability_scope(
        connected=False,
        online=False,
        last_seen_at=9_000.0,
        now=10_000.0,
    )
    unknown = subnet_member_availability_scope(
        connected=False,
        online=False,
        last_seen_at=None,
        now=10_000.0,
    )

    assert recent["scope"] == "active"
    assert recent["reason"] == "recently_offline"
    assert dormant["scope"] == "dormant"
    assert dormant["reason"] == "offline_retention"
    assert dormant["last_seen_ago_s"] == 1_000.0
    assert unknown["scope"] == "active"
    assert unknown["reason"] == "last_seen_unknown"
