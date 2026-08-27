from adaos.services.device_presence import project_device_presence


def _device(*, online: bool, last_seen_at: float) -> dict:
    return {
        "ref": "browser:device-1",
        "observation": {
            "online": online,
            "connection_state": "connected" if online else "closed",
            "last_seen_at": last_seen_at,
        },
    }


def test_device_presence_projects_online_grace_and_offline_states() -> None:
    assert project_device_presence(_device(online=True, last_seen_at=990), now=1000)["state"] == "online"

    grace = project_device_presence(
        _device(online=False, last_seen_at=990),
        now=1000,
        grace_seconds=30,
    )
    assert grace["state"] == "grace"
    assert grace["available"] is True

    offline = project_device_presence(
        _device(online=False, last_seen_at=900),
        now=1000,
        grace_seconds=30,
    )
    assert offline["state"] == "offline"
    assert offline["available"] is False
