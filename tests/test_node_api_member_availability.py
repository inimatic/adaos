from adaos.apps.api import node_api


def test_compact_member_availability_excludes_revoked_members() -> None:
    payload = {
        "role": "hub",
        "known_total": 3,
        "connected_total": 1,
        "known_members": [
            {
                "node_id": "member-online",
                "label": "Online member",
                "connected": True,
                "online": True,
                "managed_state": "managed",
            },
            {
                "node_id": "member-offline",
                "label": "Offline member",
                "connected": False,
                "managed_state": "managed",
            },
            {
                "node_id": "member-revoked",
                "label": "Retired phone",
                "connected": False,
                "managed_state": "revoked",
                "revoked": True,
            },
        ],
    }

    result = node_api._compact_member_availability(payload)

    assert result["knownTotal"] == 3
    assert result["total"] == 2
    assert result["online"] == 1
    assert result["offline"] == 1
    assert result["excluded"] == 1
    assert [item["nodeId"] for item in result["blockingMembers"]] == ["member-offline"]
